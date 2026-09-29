import type { RouteSegment } from '../types'

type AMapPoint = [number, number]
type AMapApi = Record<string, any>

export interface ResolvedAmapRoutePath {
  path: AMapPoint[]
  /** Whether the path contains provider-backed geometry suitable for a solid line. */
  hasRealGeometry: boolean
}

export interface AmapRouteDetails {
  path: AMapPoint[]
  distance_m: number | null
  duration_min: number | null
}

function toPoint(value: any): AMapPoint | null {
  if (Array.isArray(value) && value.length >= 2) {
    const longitude = Number(value[0])
    const latitude = Number(value[1])
    return Number.isFinite(longitude) && Number.isFinite(latitude)
      ? [longitude, latitude]
      : null
  }

  const longitude = typeof value?.getLng === 'function' ? value.getLng() : value?.lng
  const latitude = typeof value?.getLat === 'function' ? value.getLat() : value?.lat
  const numericLongitude = Number(longitude)
  const numericLatitude = Number(latitude)
  return Number.isFinite(numericLongitude) && Number.isFinite(numericLatitude)
    ? [numericLongitude, numericLatitude]
    : null
}

function parseCoordinateString(value: string): AMapPoint[] {
  return value.split(';').flatMap((pair) => {
    const [longitude, latitude] = pair.split(',', 2).map(Number)
    return Number.isFinite(longitude) && Number.isFinite(latitude)
      ? [[longitude, latitude] as AMapPoint]
      : []
  })
}

function collectPoints(value: any): AMapPoint[] {
  const direct = toPoint(value)
  if (direct) return [direct]
  if (typeof value === 'string') return parseCoordinateString(value)
  if (Array.isArray(value)) return value.flatMap((item) => collectPoints(item))
  return []
}

function uniquePath(points: AMapPoint[]): AMapPoint[] {
  return points.filter((point, index) => {
    const previous = points[index - 1]
    return !previous || previous[0] !== point[0] || previous[1] !== point[1]
  })
}

function endpoints(route: RouteSegment): [AMapPoint, AMapPoint] | null {
  if (
    route.origin_longitude === null || route.origin_longitude === undefined
    || route.origin_latitude === null || route.origin_latitude === undefined
    || route.destination_longitude === null || route.destination_longitude === undefined
    || route.destination_latitude === null || route.destination_latitude === undefined
  ) return null

  return [
    [route.origin_longitude, route.origin_latitude],
    [route.destination_longitude, route.destination_latitude],
  ]
}

function suppliedPath(route: RouteSegment): AMapPoint[] {
  return route.polyline && route.polyline.length >= 2
    ? uniquePath(route.polyline)
    : []
}

function extractPath(result: any, mode: RouteSegment['mode']): AMapPoint[] {
  if (mode === 'transit') {
    const plan = result?.plans?.[0]
    if (!plan) return []
    const planPath = uniquePath(collectPoints(plan.path))
    if (planPath.length >= 2) return planPath

    const segmentPath = (plan.segments || []).flatMap((segment: any) => [
      collectPoints(segment?.walking?.path),
      collectPoints(segment?.transit?.path),
      collectPoints(segment?.railway?.path),
      collectPoints(segment?.path),
    ].flat())
    return uniquePath(segmentPath)
  }

  const route = result?.routes?.[0]
  if (!route) return []
  const routePath = uniquePath(collectPoints(route.path))
  if (routePath.length >= 2) return routePath

  const stepPath = (route.steps || []).flatMap((step: any) => collectPoints(step?.path))
  return uniquePath(stepPath)
}

function ensurePlugin(AMap: AMapApi, name: string): Promise<void> {
  const serviceName = name.startsWith('AMap.') ? name.slice('AMap.'.length) : name
  if (AMap[serviceName]) return Promise.resolve()
  if (typeof AMap.plugin !== 'function') return Promise.resolve()
  return new Promise((resolve) => {
    AMap.plugin(name, () => resolve())
  })
}

function reverseGeocodeCity(AMap: AMapApi, point: AMapPoint): Promise<string | null> {
  return new Promise((resolve) => {
    try {
      const geocoder = new AMap.Geocoder()
      geocoder.getAddress(
        new AMap.LngLat(point[0], point[1]),
        (status: string, result: any) => {
          if (status !== 'complete') {
            resolve(null)
            return
          }
          const component = result?.regeocode?.addressComponent
          resolve(component?.city || component?.province || null)
        },
      )
    } catch {
      resolve(null)
    }
  })
}

/** Query the AMap JS route plugin for geometry, distance, and travel time. */
export async function fetchAmapRouteDetails(
  AMap: AMapApi,
  route: RouteSegment,
): Promise<AmapRouteDetails> {
  const empty: AmapRouteDetails = { path: [], distance_m: null, duration_min: null }
  const points = endpoints(route)
  if (!points) return empty

  const serviceName = route.mode === 'walking'
    ? 'Walking'
    : route.mode === 'driving'
      ? 'Driving'
      : 'Transfer'
  const pluginName = `AMap.${serviceName}`

  await ensurePlugin(AMap, pluginName)
  const Service = AMap[serviceName]
  if (!Service || typeof AMap.LngLat !== 'function') return empty

  return new Promise((resolve) => {
    try {
      const options: Record<string, any> = {
        hideMarkers: true,
        autoFitView: false,
      }
      if (route.mode === 'transit') {
        const cities = awaitCities(AMap, route, points)
        cities.then(([originCity, destinationCity]) => {
          if (originCity) options.city = originCity
          if (destinationCity) options.cityd = destinationCity
          searchRoute(AMap, Service, options, points, route.mode, resolve)
        })
        return
      }
      searchRoute(AMap, Service, options, points, route.mode, resolve)
    } catch {
      resolve(empty)
    }
  })
}

/** Compatibility wrapper for consumers that only need the route geometry. */
export async function fetchAmapRoutePath(
  AMap: AMapApi,
  route: RouteSegment,
): Promise<AMapPoint[]> {
  return (await fetchAmapRouteDetails(AMap, route)).path
}

async function awaitCities(
  AMap: AMapApi,
  route: RouteSegment,
  points: [AMapPoint, AMapPoint],
): Promise<[string | null, string | null]> {
  const missingCity = !route.origin_city || !route.destination_city
  if (!missingCity) return [route.origin_city!, route.destination_city!]
  await ensurePlugin(AMap, 'AMap.Geocoder')
  if (!AMap.Geocoder) return [route.origin_city || null, route.destination_city || null]
  return Promise.all([
    route.origin_city ? Promise.resolve(route.origin_city) : reverseGeocodeCity(AMap, points[0]),
    route.destination_city ? Promise.resolve(route.destination_city) : reverseGeocodeCity(AMap, points[1]),
  ])
}

function searchRoute(
  AMap: AMapApi,
  Service: any,
  options: Record<string, any>,
  points: [AMapPoint, AMapPoint],
  mode: RouteSegment['mode'],
  resolve: (details: AmapRouteDetails) => void,
) {
  const service = new Service(options)
  service.search(
    new AMap.LngLat(points[0][0], points[0][1]),
    new AMap.LngLat(points[1][0], points[1][1]),
    (status: string, result: any) => {
      if (status !== 'complete') {
        resolve({ path: [], distance_m: null, duration_min: null })
        return
      }
      const option = mode === 'transit' ? result?.plans?.[0] : result?.routes?.[0]
      const distance = Number(option?.distance)
      const durationSeconds = Number(option?.time ?? option?.duration)
      resolve({
        path: extractPath(result, mode),
        distance_m: Number.isFinite(distance) && distance >= 0 ? Math.round(distance) : null,
        duration_min: Number.isFinite(durationSeconds) && durationSeconds >= 0
          ? Math.max(1, Math.ceil(durationSeconds / 60))
          : null,
      })
    },
  )
}

/**
 * Resolve the geometry used by both the general route card and the daily plan.
 *
 * A saved backend polyline can be valid but still contain only its two
 * endpoints (for example, an older snapshot or a provider response without
 * step geometry).  Treat that as insufficient for map rendering and ask the
 * AMap JS route plugin for the road shape before falling back to the supplied
 * endpoints.  This is what prevents a real route from silently becoming a
 * straight line in the daily itinerary map.
 */
export async function resolveAmapRoutePath(
  AMap: AMapApi,
  route: RouteSegment,
): Promise<ResolvedAmapRoutePath> {
  const existing = suppliedPath(route)
  const shouldRefresh = existing.length < 3 || route.estimated || route.source === 'haversine' || route.source === 'unavailable'

  if (shouldRefresh) {
    const fetched = await fetchAmapRouteDetails(AMap, route)
    if (fetched.path.length >= 2) {
      return {
        path: fetched.path,
        hasRealGeometry: !route.estimated,
      }
    }
  }

  if (existing.length >= 2) {
    return {
      path: existing,
      // A two-point saved line is retained only as a fallback and must not be
      // presented as a verified road geometry.
      hasRealGeometry: existing.length >= 3 && !route.estimated,
    }
  }

  return {
    path: endpoints(route) || [],
    hasRealGeometry: false,
  }
}
