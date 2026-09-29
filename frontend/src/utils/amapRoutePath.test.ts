import { describe, expect, it } from 'vitest'
import type { RouteSegment } from '../types'
import { fetchAmapRoutePath, resolveAmapRoutePath } from './amapRoutePath'

function route(mode: RouteSegment['mode']): RouteSegment {
  return {
    origin_poi_id: 'origin',
    origin_name: '起点',
    destination_poi_id: 'destination',
    destination_name: '终点',
    distance_m: 1000,
    duration_min: 20,
    origin_longitude: 116.1,
    origin_latitude: 39.9,
    destination_longitude: 116.4,
    destination_latitude: 39.95,
    mode,
    source: 'amap_mcp',
    estimated: false,
  }
}

describe('fetchAmapRoutePath', () => {
  it('extracts driving geometry from route steps', async () => {
    class LngLat {
      constructor(public lng: number, public lat: number) {}
    }

    const AMap: Record<string, any> = {
      LngLat,
      Driving: class {
        search(_origin: unknown, _destination: unknown, callback: Function) {
          callback('complete', {
            routes: [{
              steps: [
                { path: [[116.1, 39.9], [116.2, 39.92]] },
                { path: [[116.2, 39.92], [116.4, 39.95]] },
              ],
            }],
          })
        }
      },
    }

    await expect(fetchAmapRoutePath(AMap, route('driving'))).resolves.toEqual([
      [116.1, 39.9],
      [116.2, 39.92],
      [116.4, 39.95],
    ])
  })

  it('extracts transit geometry from transit segments', async () => {
    class LngLat {
      constructor(public lng: number, public lat: number) {}
    }

    const AMap: Record<string, any> = {
      LngLat,
      Transfer: class {
        search(_origin: unknown, _destination: unknown, callback: Function) {
          callback('complete', {
            plans: [{
              segments: [
                { transit: { path: [{ lng: 116.1, lat: 39.9 }, { lng: 116.3, lat: 39.93 }] } },
                { transit: { path: [{ lng: 116.3, lat: 39.93 }, { lng: 116.4, lat: 39.95 }] } },
              ],
            }],
          })
        }
      },
    }

    await expect(fetchAmapRoutePath(AMap, route('transit'))).resolves.toEqual([
      [116.1, 39.9],
      [116.3, 39.93],
      [116.4, 39.95],
    ])
  })

  it('fills missing cities for historical transit route cards', async () => {
    let transferOptions: Record<string, any> | null = null
    class LngLat {
      constructor(public lng: number, public lat: number) {}
    }

    const AMap: Record<string, any> = {
      LngLat,
      Transfer: class {
        constructor(options: Record<string, any>) {
          transferOptions = options
        }

        search(_origin: unknown, _destination: unknown, callback: Function) {
          callback('complete', {
            plans: [{
              segments: [{ transit: { path: [[116.1, 39.9], [116.4, 39.95]] } }],
            }],
          })
        }
      },
      plugin(name: string, callback: Function) {
        if (name === 'AMap.Geocoder') {
          AMap.Geocoder = class {
            getAddress(_point: unknown, geocodeCallback: Function) {
              geocodeCallback('complete', {
                regeocode: { addressComponent: { city: '北京市', province: '北京市' } },
              })
            }
          }
        }
        callback()
      },
    }

    await expect(fetchAmapRoutePath(AMap, route('transit'))).resolves.toEqual([
      [116.1, 39.9],
      [116.4, 39.95],
    ])
    expect(transferOptions).toMatchObject({ city: '北京市', cityd: '北京市' })
  })

  it('refreshes a sparse saved polyline before rendering the route', async () => {
    class LngLat {
      constructor(public lng: number, public lat: number) {}
    }

    const AMap: Record<string, any> = {
      LngLat,
      Driving: class {
        search(_origin: unknown, _destination: unknown, callback: Function) {
          callback('complete', {
            routes: [{
              steps: [{
                path: [
                  [116.1, 39.9],
                  [116.2, 39.92],
                  [116.4, 39.95],
                ],
              }],
            }],
          })
        }
      },
    }

    const result = await resolveAmapRoutePath(AMap, {
      ...route('driving'),
      polyline: [[116.1, 39.9], [116.4, 39.95]],
    })

    expect(result.path).toEqual([
      [116.1, 39.9],
      [116.2, 39.92],
      [116.4, 39.95],
    ])
    expect(result.hasRealGeometry).toBe(true)
  })
})
