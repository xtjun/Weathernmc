import logging
import re
from datetime import datetime, timedelta

import asyncio
import async_timeout
import aiohttp

import voluptuous as vol

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from homeassistant.components.weather import (
    WeatherEntity,
    WeatherEntityFeature,
    Forecast,
    ATTR_FORECAST_CONDITION,
    ATTR_FORECAST_NATIVE_TEMP,
    ATTR_FORECAST_NATIVE_TEMP_LOW,
    ATTR_FORECAST_TIME,
    ATTR_FORECAST_HUMIDITY,
    ATTR_FORECAST_NATIVE_PRECIPITATION,
    ATTR_FORECAST_WIND_BEARING,
    ATTR_FORECAST_WIND_SPEED,
    ATTR_FORECAST_NATIVE_WIND_SPEED,
    PLATFORM_SCHEMA)
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfVolumetricFlux,
    UnitOfTemperature
)

import homeassistant.helpers.config_validation as cv
import homeassistant.util.dt as dt_util

_LOGGER = logging.getLogger(__name__)

TIME_BETWEEN_UPDATES = timedelta(seconds=1800)
HOURLY_TIME_BETWEEN_UPDATES = timedelta(seconds=1800)

DEFAULT_TIME = dt_util.now()

CONF_STATIONID = "stationId"
CONF_NAME = "name"

# 状态翻译
CONDITION_MAP = {
    '晴': 'sunny',
    '多云': 'cloudy',
    '局部多云': 'partlycloudy',
    '阴': 'partlycloudy',
    '薄雾': 'fog',
    '雾': 'fog',
    '中雾': 'fog',
    '大雾': 'fog',
    '扬沙': 'fog',
    '沙尘': 'fog',
    '雨': 'rainy',
    '小雨': 'rainy',
    '中雨': 'rainy',
    '冻雨': 'rainy',
    '大雨': 'pouring',
    '暴雨': 'pouring',
    '雷阵雨': 'lightning-rainy',
    '雪': 'snowy',
    '小雪': 'snowy',
    '中雪': 'snowy',
    '大雪': 'snowy',
    '暴雪': 'snowy',
    '雨夹雪': 'snowy-rainy',
    '风': 'windy',
    '有风': 'windy',
    '大风': 'windy-variant',
    '飓风': 'hurricane',
    '冰雹': 'hail',
    '9999': 'exceptional',
    '未知': 'exceptional',
    '阵雨': 'rainy',
    '雷阵雨伴有冰雹': 'lightning-rainy',
    '小到中雨': 'rainy',
    '中到大雨': 'rainy',
    '大到暴雨': 'pouring',
    '暴雨到大暴雨': 'pouring',
    '浮尘': 'fog',
    '沙尘暴': 'windy-variant',
    '强沙尘暴': 'windy-variant',
    '阵雪': 'snowy',
}


NMC_IMG_TEXT = {
    '0': '晴', '1': '多云', '2': '阴', '3': '阵雨',
    '4': '雷阵雨', '5': '雷阵雨伴有冰雹',
    '6': '雨夹雪', '7': '小雨', '8': '中雨', '9': '大雨',
    '10': '暴雨', '11': '大暴雨', '12': '特大暴雨',
    '13': '阵雪', '14': '小雪', '15': '中雪', '16': '大雪',
    '17': '暴雪', '18': '雾', '19': '冻雨', '20': '沙尘暴',
    '21': '小到中雨', '22': '中到大雨', '23': '大到暴雨',
    '24': '暴雨到大暴雨', '25': '大暴雨到特大暴雨',
    '26': '小到中雪', '27': '中到大雪', '28': '大到暴雪',
    '29': '浮尘', '30': '扬沙', '31': '强沙尘暴',
    '9999': '未知',
}


async def _weathernmc_fetch_hourly(station_url):
    """解析 nmc 站点 HTML 页面里的逐小时预报."""
    if not station_url:
        return []
    url = "https://www.nmc.cn" + station_url
    try:
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={'User-Agent': 'Mozilla/5.0'}) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    _LOGGER.error("weathernmc hourly http status %s", resp.status)
                    return []
                html = await resp.text()
    except (asyncio.TimeoutError, aiohttp.ClientError) as err:
        _LOGGER.warning("weathernmc hourly fetch error: %s", err)
        return []

    idx = html.find('hourItems')
    if idx == -1:
        return []
    seg = html[idx:]

    day_blocks = list(re.finditer(r'id=(day\d+)[^>]*>', seg))
    now = datetime.now()
    today = now.date()
    seen = {}

    for bi, m in enumerate(day_blocks):
        block_end = day_blocks[bi + 1].start() if bi + 1 < len(day_blocks) else len(seg)
        block = seg[m.start():block_end]
        items = list(re.finditer(
            r'<div class="?hour3[^"]*"[^>]*>(.*?)</div>\s*</div>', block, re.S))
        if not items:
            continue

        anchor_j = None
        anchor_mday = None
        for j, it in enumerate(items):
            dm = re.search(r'(\d{1,2})日', it.group(1))
            if dm:
                anchor_j = j
                anchor_mday = int(dm.group(1))
                break
        if anchor_j is None:
            continue

        tm = re.search(r'(\d{1,2}):(\d{2})', items[anchor_j].group(1))
        try:
            a_date = today.replace(day=anchor_mday)
            if anchor_mday < today.day:
                if a_date.month == 12:
                    a_date = a_date.replace(year=a_date.year + 1, month=1)
                else:
                    a_date = a_date.replace(month=a_date.month + 1)
            if tm:
                anchor_dt = datetime.combine(a_date, datetime.min.time()).replace(
                    hour=int(tm.group(1)), minute=int(tm.group(2)))
            else:
                anchor_dt = datetime.combine(a_date, datetime.min.time())
        except ValueError:
            continue

        for j, it in enumerate(items):
            inner = it.group(1)
            dt = anchor_dt + timedelta(hours=3 * (j - anchor_j))
            if dt <= now:
                continue
            key = dt.isoformat()
            if key in seen:
                continue

            tcode = re.search(r'/w/40x40/3/(\d+)\.png', inner)
            code = tcode.group(1) if tcode else None
            text = NMC_IMG_TEXT.get(code, '未知') if code else '未知'

            temp = re.search(r'<div[^>]*>\s*(-?\d+\.?\d*)℃', inner)
            wspd = re.search(r'<div[^>]*>\s*(-?\d+\.?\d*)m/s', inner)
            wdir = re.search(r'<div[^>]*>\s*([\u4e00-\u9fa5]*?风)\s*</div>', inner)
            hum = re.search(r'<div[^>]*>\s*(-?\d+\.?\d*)%', inner)

            entry = {
                ATTR_FORECAST_TIME: dt,
                ATTR_FORECAST_CONDITION: CONDITION_MAP.get(text, 'unknown'),
                ATTR_FORECAST_NATIVE_TEMP: float(temp.group(1)) if temp else None,
                ATTR_FORECAST_WIND_BEARING: wdir.group(1) if wdir else None,
                ATTR_FORECAST_NATIVE_WIND_SPEED: round(float(wspd.group(1)) * 3.6, 1) if wspd else None,
                'text': text,
            }
            if hum:
                entry[ATTR_FORECAST_HUMIDITY] = float(hum.group(1))
            seen[key] = entry

    return [seen[k] for k in sorted(seen)]


ATTR_UPDATE_TIME = "更新时间"
ATTRIBUTION = "来自nmc.cn的天气数据"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_STATIONID): cv.string,
    vol.Required(CONF_NAME): cv.string,
})


# @asyncio.coroutine
async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    _LOGGER.info("setup platform weathernmc")

    station_id = config.get(CONF_STATIONID)
    name = config.get(CONF_NAME)

    async_add_devices([NmcWeather(station_id, name)], True)


class NmcWeather(WeatherEntity):
    """Representation of a weather condition."""

    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_precipitation_unit = UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_visibility_unit = UnitOfLength.KILOMETERS

    def __init__(self, station_id, name):
        _LOGGER.info("init platform weathernmc")

        self._url = "http://www.nmc.cn/rest/weather?stationid=" + station_id

        self._name = name
        self._condition = None
        self._temperature = None
        self._humidity = None
        self._pressure = None
        self._wind_speed = None
        self._wind_bearing = None
        self._visibility = None
        self._precipitation = None

        self._dew = None
        self._feelslike = None
        self._cloud = None

        self._aqi = None
        self._aqi_description = None
        self._alert = None

        self._updatetime = None
        self._forecast = None
        self._forecast_hourly = None

        self._object_id = 'localweather'
        self._attr_unique_id = 'weather_' + station_id

        self._attr_supported_features = 0
        self._attr_supported_features = WeatherEntityFeature.FORECAST_DAILY
        self._attr_supported_features |= WeatherEntityFeature.FORECAST_HOURLY

    @property
    def name(self):
        """返回实体的名字."""
        return self._name

    @property
    def should_poll(self):
        """attention No polling needed for a demo weather condition."""
        return True

    @property
    def native_dew_point(self):
        """露点温度"""
        return self._dew

    @property
    def native_apparent_temperature(self):
        """体感温度"""
        return self._feelslike

    @property
    def cloud_coverage(self):
        """云量"""
        return self._cloud

    @property
    def native_temperature(self):
        """温度"""
        return self._temperature

    @property
    def native_temperature_unit(self):
        """温度单位"""
        return self._attr_native_temperature_unit

    @property
    def humidity(self):
        """湿度."""
        return self._humidity

    @property
    def native_wind_speed(self):
        """风速"""
        return self._wind_speed

    @property
    def wind_bearing(self):
        """风向"""
        return self._wind_bearing

    @property
    def native_pressure(self):
        """气压"""
        return self._pressure

    @property
    def native_visibility(self):
        """能见度"""
        return self._visibility

    @property
    def native_precipitation(self):
        """当前小时累计降水量"""
        return self._precipitation

    @property
    def condition(self):
        """天气情况"""
        if self._condition:
            match_status = CONDITION_MAP.get(self._condition, "unknown")
            return match_status if match_status else 'unknown'
        else:
            return 'unknown'

    @property
    def attribution(self):
        """归属信息"""
        return 'Powered by NMC.CN'

    @property
    def device_state_attributes(self):
        """设置其它一些属性值"""
        if self._condition is not None:
            return {
                ATTR_ATTRIBUTION: ATTRIBUTION,
                ATTR_UPDATE_TIME: self._updatetime
            }

    # 空气质量
    @property
    def aqi(self):
        return self._aqi

    # 空气质量描述
    # @property
    # def aqi_description(self):
    #     return self._aqi_description

    # 预警
    # @property
    # def alert(self):
    #     return '无' if self._alert == '9999' else self._alert

    @property
    def forecast(self):
        """天预报"""
        return self._forecast

    @property
    def forecast_hourly(self):
        """小时预报"""
        return self.forecast_hourly

    async def async_forecast_daily(self) -> list[Forecast]:
        """天预报"""
        return self._forecast

    async def async_forecast_hourly(self) -> list[Forecast]:
        """小时预报"""
        return self._forecast_hourly

    # @asyncio.coroutine
    async def async_update(self, now=DEFAULT_TIME):
        """从远程更新信息."""
        _LOGGER.info("update weather from nmc.cn ")

        # 通过HTTP访问，获取需要的信息
        # 此处使用了基于aiohttp库的async_get_clientsession
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            connector = aiohttp.TCPConnector(limit=10)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get(self._url) as response:
                    json_data = await response.json()
                    weather = json_data["data"]
                    _LOGGER.info("get weathernmc info: %s", json_data)
        except(asyncio.TimeoutError, aiohttp.ClientError):
            _LOGGER.error("weathernmc Error while accessing: %s", self._url)
            return

        # 数据处理
        # 实时观测常为 '-'/'9999' 占位符; 按优先级取第一个有效现象:
        # 实时 -> 今天日间 -> 今天夜间 -> 明天…… (跳过占位符)
        _placeholders = ("-", "9999", "", "无", None)
        _info = weather['real']['weather']['info']
        if _info in _placeholders:
            _cands = []
            for _d in weather['predict']['detail'][:2]:
                _cands.extend((_d['day']['weather']['info'],
                               _d['night']['weather']['info']))
            _info = next((c for c in _cands if c not in _placeholders), "未知")
        self._condition = _info  # 天气
        self._temperature = float(weather['real']['weather']['temperature'])  # 温度
        self._humidity = float(weather['real']['weather']['humidity'])  # 湿度
        self._pressure = weather['passedchart'][0]['pressure']  # 气压
        self._wind_speed = weather['real']['wind']['power']  # 风速
        self._wind_bearing = weather['real']['wind']['direct']  # 风向
        self._precipitation = float(weather['passedchart'][0]['rain1h'])  # 降水量
        self._feelslike = float(weather['real']['weather']['feelst'])  # 体感温度

        self._aqi = weather['air']['aqi']  # 空气质量
        self._aqi_description = weather['air']['text']  # 空气质量描述
        self._alert = weather['real']['warn']['alert']  # 预警

        # self._dew = float(weather['real']['weather']["dew"]) #露点温度
        # self._cloud = int(weather["cloud"]) #云量
        # self._visibility = weather["vis"] #能见度

        self._updatetime = weather["real"]["publish_time"]

        forecast_data = []
        for i in range(0, 7):
            time_str = weather['predict']['detail'][i]['date']
            dayInfo = weather['predict']['detail'][i]['day']
            if dayInfo['weather']['info'] == "9999":
                dayInfo = weather['predict']['detail'][i]['night']

            data_dict = {
                ATTR_FORECAST_TIME: datetime.strptime(time_str, '%Y-%m-%d'),
                ATTR_FORECAST_NATIVE_TEMP: weather['tempchart'][i + 7]['max_temp'],
                ATTR_FORECAST_NATIVE_TEMP_LOW: weather['tempchart'][i + 7]['min_temp'],
                ATTR_FORECAST_CONDITION: CONDITION_MAP.get(dayInfo['weather']['info'], 'unknown'),
                ATTR_FORECAST_WIND_BEARING: dayInfo['wind']['direct'],
                ATTR_FORECAST_WIND_SPEED: dayInfo['wind']['power'],
                'text': dayInfo['weather']['info']
            }
            if datetime.strptime(time_str, "%Y-%m-%d").date() >= datetime.now().date():
                forecast_data.append(data_dict)

        self._forecast = forecast_data
        try:
            _station_url = weather.get('real', {}).get('station', {}).get('url')
            self._forecast_hourly = await _weathernmc_fetch_hourly(_station_url)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("weathernmc hourly parse error: %s", err)
            self._forecast_hourly = []

        _LOGGER.info("success to load weathernmc")
