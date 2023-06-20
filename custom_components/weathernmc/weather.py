import json
import time
from datetime import datetime
from typing import final

import requests
from homeassistant.components.weather import (
    WeatherEntity,
    ATTR_FORECAST_CONDITION,
    ATTR_FORECAST_TEMP,
    ATTR_FORECAST_TEMP_LOW,
    ATTR_FORECAST_TIME,
    ATTR_FORECAST_WIND_BEARING,
    ATTR_FORECAST_WIND_SPEED)
from homeassistant.const import (TEMP_CELSIUS,
                                 PRESSURE_HPA,
                                 PRECIPITATION_MILLIMETERS_PER_HOUR)

VERSION = '1.0.0'
DOMAIN = 'weathernmc'

# hass状态列表
# {
#   "clear-night": "晴夜",
#   "cloudy": "多云",
#   "exceptional": "特别",
#   "fog": "雾",
#   "hail": "冰雹",
#   "lightning": "闪电",
#   "lightning-rainy": "雷雨",
#   "partlycloudy": "局部多云",
#   "pouring": "Pouring",
#   "rainy": "下雨",
#   "snowy": "下雪",
#   "snowy-rainy": "雨夹雪",
#   "sunny": "晴天",
#   "windy": "有风",
#   "windy-variant": "风"
# }

# 状态翻译
CONDITION_MAP = {
    '晴': 'sunny',
    '多云': 'cloudy',
    '局部多云': 'partlycloudy',
    '阴': 'cloudy',
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
    '雨夹雪': 'snowy-rainy',
    '雪': 'snowy',
    '小雪': 'snowy',
    '中雪': 'snowy',
    '大雪': 'snowy',
    '暴雪': 'snowy',
    '大风': 'windy',
    '冰雹': 'hail',
    '9999': 'exceptional',

}


def setup_platform(hass, config, add_entities, discovery_info=None):
    add_entities([NMCWeather(config.get('code'),config.get('name','weathernmc'))])


class NMCWeather(WeatherEntity):

    def __init__(self, code: str,name: str):
        self._forecast_data = None
        self._code = code
        self._name = name
        self._url = str.format("http://www.nmc.cn/rest/weather?stationid="+code)

        self.update()

    @property
    def name(self):
        return self._name

    # 当前状态
    @property
    def state(self):
        return CONDITION_MAP[self._forecast_data['real']['weather']['info']]

    # 底部说明
    @property
    def attribution(self):
        return 'Powered by www.nmc.cn'

    #气温
    @property
    def temperature(self):
        return self._forecast_data['real']['weather']['temperature']

    # 气温单位
    @property
    def temperature_unit(self):
        return TEMP_CELSIUS

    # 气压
    @property
    def pressure(self):
        return self._forecast_data['passedchart'][0]['pressure']

    # 气压单位
    @property
    def pressure_unit(self):
        return PRESSURE_HPA

    # 湿度
    @property
    def humidity(self):
        return float(self._forecast_data['real']['weather']['humidity'])

    # 风速
    @property
    def wind_speed(self):
        return self._forecast_data['passedchart'][0]['windSpeed']

    # 风向
    @property
    def wind_bearing(self):
        return self._forecast_data['real']['wind']['direct']

    # 能见度
    # @property
    # def visibility(self):
    #     return 0

    # # 能见度单位
    # @property
    # def visibility_unit(self):
    #     return LENGTH_KILOMETERS

    # 降水量
    @property
    def precipitation(self):
        return self._forecast_data['passedchart'][0]['rain1h']

    # 降水量单位
    @property
    def precipitation_unit(self):
        return PRECIPITATION_MILLIMETERS_PER_HOUR

    # 空气质量
    @property
    def aqi(self):
        return self._forecast_data.get('air',{}).get('aqi', '')

    # 空气质量描述
    @property
    def aqi_description(self):
        return self._forecast_data.get('air',{}).get('text', '')

    # 预警
    @final
    @property
    def alert(self):
        return self._forecast_data['real']['warn']['alert']

    # 状态属性
    @property
    def state_attributes(self):
        data = super(NMCWeather, self).state_attributes
        data['aqi'] = self.aqi
        return data

    @property
    def forecast(self):
        forecast_data = []
        for i in range(1, 7):
            time_str = self._forecast_data['predict']['detail'][i]['date']
            data_dict = {
                ATTR_FORECAST_TIME: datetime.strptime(time_str, '%Y-%m-%d'),
                ATTR_FORECAST_CONDITION: CONDITION_MAP[self._forecast_data['predict']['detail'][i]['day']['weather']['info']],
                ATTR_FORECAST_TEMP: self._forecast_data['tempchart'][i + 7]['max_temp'],
                ATTR_FORECAST_TEMP_LOW: self._forecast_data['tempchart'][i + 7]['min_temp'],
                ATTR_FORECAST_WIND_BEARING: self._forecast_data['predict']['detail'][i]['day']['wind']['direct'],
                ATTR_FORECAST_WIND_SPEED: self._forecast_data['predict']['detail'][i]['day']['wind']['power']
            }
            forecast_data.append(data_dict)

        return forecast_data

    def update(self):
        update_result = False
        try:
            if self._code is not None:
                print("NMCWeather start update：",self._url)
                self._forecast_data = requests.get(self._url).json()['data']
            update_result = True
        except Exception as e:
            print("NMCWeather update error", e)
        if update_result:
            return
        else:
            print("NMCWeather update failed, retry in 5s.")
            try:
                time.sleep(5)
            except Exception as e:
                print("NMCWeather update sleep error", e)
            self.update()
