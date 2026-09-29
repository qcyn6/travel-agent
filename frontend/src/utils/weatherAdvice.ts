import type { WeatherResult } from '../types'

export function fallbackWeatherAdvice(weather: WeatherResult): string {
  const condition = weather.condition?.trim() || ''
  const normalized = condition.toLowerCase()
  const advice: string[] = []
  const precipitationCondition = ['雨', '雪', 'rain', 'snow', 'shower', 'storm']
    .some((word) => normalized.includes(word))

  if (precipitationCondition) {
    advice.push('有降水，建议带伞或雨衣，走路时留意路面湿滑')
  } else if (['晴', 'sunny', 'clear'].some((word) => normalized.includes(word))) {
    advice.push('晴天适合户外游览，记得防晒并及时补水')
  } else if (['多云', '少云', '阴', 'cloud', 'overcast'].some((word) => normalized.includes(word))) {
    advice.push('多云天气适合户外游览，可按早晚温差灵活添衣')
  } else if (condition) {
    advice.push(`天气为${condition}，建议随身准备适合天气的衣物`)
  } else {
    advice.push('出行前留意天气变化，并准备适合当天的衣物')
  }

  if (
    weather.precipitation_probability !== null
    && weather.precipitation_probability >= 0.5
    && !precipitationCondition
  ) {
    advice.push('降水概率较高，建议随身带伞')
  }

  if (weather.temperature_max !== null && weather.temperature_max >= 32) {
    advice.push('天气较热时注意防晒、补水和适当休息')
  } else if (weather.temperature_min !== null && weather.temperature_min <= 5) {
    advice.push('气温偏低，建议增加保暖衣物')
  } else if (
    weather.temperature_min !== null
    && weather.temperature_max !== null
    && weather.temperature_max - weather.temperature_min >= 10
  ) {
    advice.push('昼夜温差较大，建议分层穿衣')
  }

  return advice.join('；').slice(0, 240)
}
