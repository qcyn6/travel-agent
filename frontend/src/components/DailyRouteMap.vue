<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { TravelDay } from '../types'
import { amapNavigationUrl } from '../utils/amapNavigation'
import { resolveAmapRoutePath } from '../utils/amapRoutePath'

const props = defineProps<{ day: TravelDay }>()

const mapElement = ref<HTMLElement | null>(null)
const mapReady = ref(false)
const mapMessage = ref('')
let mapInstance: any = null
let renderVersion = 0

const routeStops = computed(() => [
  ...(props.day.start_hotel ? [props.day.start_hotel] : []),
  ...props.day.attractions,
  ...(props.day.hotel ? [props.day.hotel] : []),
])
function hasCoordinates(stop: { longitude?: number | null; latitude?: number | null }) {
  return typeof stop.longitude === 'number'
    && Number.isFinite(stop.longitude)
    && typeof stop.latitude === 'number'
    && Number.isFinite(stop.latitude)
}

const locatedStops = computed(() => routeStops.value.filter(
  hasCoordinates,
))
const canShowRoute = computed(() => locatedStops.value.length >= 2)
const routeSegments = computed(() => [
  ...(props.day.start_hotel_route ? [props.day.start_hotel_route] : []),
  ...props.day.routes,
  ...(props.day.hotel_route ? [props.day.hotel_route] : []),
])
const routeLinks = computed(() => routeSegments.value.flatMap((route, index) => {
  // Older saved routes may have names and mode but no endpoint coordinates.
  // Recover them from the adjacent stops so the navigation action survives
  // snapshots created before route coordinates were persisted.
  const origin = routeStops.value.find((stop) => stop.poi_id === route.origin_poi_id)
    || routeStops.value[index]
  const destination = routeStops.value.find((stop) => stop.poi_id === route.destination_poi_id)
    || routeStops.value[index + 1]
  const url = amapNavigationUrl(route, origin, destination)
  return url ? [{ route, url }] : []
}))
const jsKey = import.meta.env.VITE_AMAP_JS_KEY?.trim() || ''
const securityCode = import.meta.env.VITE_AMAP_SECURITY_CODE?.trim() || ''

function routeStopLabel(index: number) {
  if (props.day.start_hotel && index === 0) return '昨'
  const attractionIndex = index - (props.day.start_hotel ? 1 : 0)
  if (attractionIndex >= props.day.attractions.length) return '住'
  return String(attractionIndex + 1)
}

declare global {
  interface Window {
    AMap?: any
    _AMapSecurityConfig?: { securityJsCode: string }
  }
}

let amapLoader: Promise<any> | null = null

function loadAmap() {
  if (window.AMap) return Promise.resolve(window.AMap)
  if (amapLoader) return amapLoader
  if (!jsKey) return Promise.reject(new Error('未配置高德地图 JS Key'))
  if (securityCode) window._AMapSecurityConfig = { securityJsCode: securityCode }
  amapLoader = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(jsKey)}`
    script.async = true
    script.onload = () => window.AMap ? resolve(window.AMap) : reject(new Error('高德地图加载失败'))
    script.onerror = () => reject(new Error('高德地图加载失败'))
    document.head.appendChild(script)
  })
  return amapLoader
}

function destroyMap() {
  if (mapInstance) mapInstance.destroy()
  mapInstance = null
  mapReady.value = false
}

async function renderMap() {
  const version = ++renderVersion
  destroyMap()
  mapMessage.value = ''
  if (!canShowRoute.value) {
    mapMessage.value = '当前景点坐标不足，暂时无法显示路线地图'
    return
  }
  if (!jsKey) {
    mapMessage.value = '高德地图 JS Key 尚未配置，当前显示路线示意'
    return
  }
  await nextTick()
  if (!mapElement.value) return
  try {
    const AMap = await loadAmap()
    if (version !== renderVersion || !mapElement.value) return
    const map = new AMap.Map(mapElement.value, {
      viewMode: '2D',
      zoom: 12,
      mapStyle: 'amap://styles/light',
      features: ['bg', 'road'],
      resizeEnable: true,
    })
    mapInstance = map
    const overlays: any[] = []
    const resolvedSegments = await Promise.all(
      routeSegments.value.map(async (route) => {
        const resolved = await resolveAmapRoutePath(AMap, route)
        return { route, ...resolved }
      }),
    )
    if (version !== renderVersion || !mapElement.value) return
    routeStops.value.forEach((stop, index) => {
      if (!hasCoordinates(stop)) return
      const isHotel = (props.day.start_hotel && index === 0)
        || (props.day.hotel && index === routeStops.value.length - 1)
      const marker = new AMap.Marker({
        position: [stop.longitude, stop.latitude],
        anchor: 'center',
        content: `<span class="${isHotel ? 'amap-hotel-marker' : 'amap-route-marker'}">${routeStopLabel(index)}</span>`,
        title: stop.name,
      })
      overlays.push(marker)
      map.add(marker)
    })
    resolvedSegments.forEach(({ route, path, hasRealGeometry }) => {
      if (path.length < 2) return
      const verified = hasRealGeometry && !route.estimated
      const line = new AMap.Polyline({
        path,
        strokeColor: route.mode === 'walking' ? '#db8b5a' : route.mode === 'transit' ? '#2b8279' : '#315f73',
        strokeWeight: 6,
        strokeOpacity: 0.86,
        strokeStyle: verified ? 'solid' : 'dashed',
        lineJoin: 'round',
        lineCap: 'round',
        showDir: verified,
      })
      overlays.push(line)
      map.add(line)
    })
    map.setFitView(overlays, false, [44, 44, 44, 44], 16)
    mapReady.value = true
  } catch (error) {
    mapMessage.value = error instanceof Error ? error.message : '高德路线地图加载失败'
  }
}

watch(() => props.day, renderMap, { deep: true, immediate: true })
onBeforeUnmount(() => {
  renderVersion += 1
  destroyMap()
})
</script>

<template>
  <section v-if="canShowRoute" class="daily-route-map" aria-label="当天景点移动地图">
    <header>
      <div class="daily-route-map-title"><span>ROUTE MAP</span><strong>当天移动路线</strong></div>
      <div class="daily-route-map-tools">
        <small>实线为高德真实路线，虚线为距离估算</small>
      </div>
    </header>
    <div ref="mapElement" class="daily-route-map-canvas" :class="{ ready: mapReady }"></div>
    <div v-if="!mapReady" class="route-map-fallback">
      <div class="route-map-track">
        <template v-for="(stop, index) in routeStops" :key="stop.poi_id">
          <div class="route-map-stop"><i>{{ routeStopLabel(index) }}</i><span>{{ stop.name }}</span></div>
          <b v-if="index < routeStops.length - 1">→</b>
        </template>
      </div>
      <p>{{ mapMessage || '正在加载高德路线地图…' }}</p>
    </div>
    <div v-if="routeLinks.length" class="daily-route-map-navigation">
      <div class="daily-route-map-navigation-heading">
        <strong>高德分段导航</strong>
        <small>按行程顺序逐段打开路线</small>
      </div>
      <nav class="daily-route-map-links" aria-label="使用高德地图导航">
        <a
          v-for="{ route, url } in routeLinks"
          :key="`${route.origin_poi_id}-${route.destination_poi_id}`"
          :href="url"
          target="_blank"
          rel="noopener noreferrer"
        >
          ↗ {{ route.origin_name }} → {{ route.destination_name }}
        </a>
      </nav>
    </div>
    <p v-else-if="routeSegments.length" class="daily-route-map-link-note">
      路线坐标不完整，暂时无法生成高德导航链接
    </p>
  </section>
</template>
