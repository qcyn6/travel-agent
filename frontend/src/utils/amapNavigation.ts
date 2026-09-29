import type { RouteSegment } from '../types'

const amapMode: Record<RouteSegment['mode'], string> = {
  walking: 'walk',
  transit: 'bus',
  driving: 'car',
}

interface NavigationStop {
  name?: string | null
  longitude?: number | null
  latitude?: number | null
}

function coordinate(value: number | null | undefined, fallback: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

export function amapNavigationUrl(
  route: RouteSegment,
  origin?: NavigationStop,
  destination?: NavigationStop,
): string | null {
  const originLongitude = coordinate(route.origin_longitude, origin?.longitude)
  const originLatitude = coordinate(route.origin_latitude, origin?.latitude)
  const destinationLongitude = coordinate(route.destination_longitude, destination?.longitude)
  const destinationLatitude = coordinate(route.destination_latitude, destination?.latitude)
  const coordinates = [
    originLongitude,
    originLatitude,
    destinationLongitude,
    destinationLatitude,
  ]
  if (!coordinates.every((value) => typeof value === 'number' && Number.isFinite(value))) {
    return null
  }

  const url = new URL('https://uri.amap.com/navigation')
  url.searchParams.set(
    'from',
    `${originLongitude},${originLatitude},${route.origin_name || origin?.name || ''}`,
  )
  url.searchParams.set(
    'to',
    `${destinationLongitude},${destinationLatitude},${route.destination_name || destination?.name || ''}`,
  )
  url.searchParams.set('mode', amapMode[route.mode])
  url.searchParams.set('src', 'travel-agent')
  url.searchParams.set('callnative', '1')
  return url.toString()
}
