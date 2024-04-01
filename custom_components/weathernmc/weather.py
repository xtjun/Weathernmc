import logging
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
}

ATTR_UPDATE_TIME = "更新时间"
ATTRIBUTION = "来自nmc.cn的天气数据"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_STATIONID): cv.string,
    vol.Required(CONF_NAME): cv.string,
})


# @asyncio.coroutine
async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the hefeng weather."""
    _LOGGER.info("setup platform weather.Heweather...")

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
        """Initialize the  weather."""
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
            match_status = CONDITION_MAP[self._condition]
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
                async with session.get(self. _url) as response:
                    json_data = await response.json()
                    weather = json_data["data"]
        except(asyncio.TimeoutError, aiohttp.ClientError):
            _LOGGER.error("Error while accessing: %s", self._url)
            return

        # 数据处理
        self._condition = weather['real']['weather']['info']                     #天气
        self._temperature = float(weather['real']['weather']['temperature'])     #温度
        self._humidity = float(weather['real']['weather']['humidity'])           #湿度
        self._pressure = weather['passedchart'][0]['pressure']                   #气压
        self._wind_speed = weather['real']['wind']['power']                      #风速
        self._wind_bearing = weather['real']['wind']['direct']                   #风向
        self._precipitation = float(weather['passedchart'][0]['rain1h'])         #降水量
        self._feelslike = float(weather['real']['weather']['feelst'])            #体感温度

        self._aqi = weather['air']['aqi']                         #空气质量
        self._aqi_description = weather['air']['text']                   #空气质量描述
        self._alert = weather['real']['warn']['alert']                   #预警

        # self._dew = float(weather['real']['weather']["dew"]) #露点温度
        # self._cloud = int(weather["cloud"]) #云量
        # self._visibility = weather["vis"] #能见度

        self._updatetime = weather["real"]["publish_time"]

        forecast_data = []
        for i in range(0, 7):
            time_str = weather['predict']['detail'][i]['date']
            data_dict = {
                ATTR_FORECAST_TIME: datetime.strptime(time_str, '%Y-%m-%d'),
                ATTR_FORECAST_CONDITION: CONDITION_MAP[weather['predict']['detail'][i]['day']['weather']['info']],
                ATTR_FORECAST_NATIVE_TEMP: weather['tempchart'][i + 7]['max_temp'],
                ATTR_FORECAST_NATIVE_TEMP_LOW: weather['tempchart'][i + 7]['min_temp'],
                ATTR_FORECAST_WIND_BEARING: weather['predict']['detail'][i]['day']['wind']['direct'],
                ATTR_FORECAST_WIND_SPEED: weather['predict']['detail'][i]['day']['wind']['power'],
                'text': weather['predict']['detail'][i]['day']['weather']['info']
            }
            if datetime.strptime(time_str, "%Y-%m-%d").date() >= datetime.now().date():
                forecast_data.append(data_dict)

        self._forecast = forecast_data
        self._forecast_hourly = []


        _LOGGER.info("success to load local informations")
