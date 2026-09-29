<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { RouteSegment } from '../types'
import { loadAmap } from '../utils/amapLoader'
import { fetchAmapRouteDetails, resolveAmapRoutePath } from '../utils/amapRoutePath'

const props = defineProps<{ route: RouteSegment; disabled?: boolean }>()
const emit = defineEmits<{ 'update:route': [route: RouteSegment] }>()

const mapElement = ref<HTMLElement | null>(null)
const mapReady = ref(false)
const mapMessage = ref('')
const modeError = ref('')
const modeLoading = ref(false)
const activeRoute = ref<RouteSegment>(props.route)
let mapInstance: any = null
let renderVersion = 0
let modeRequestVersion = 0
let resizeTimer: number | null = null

const routeModes: Array<{ value: RouteSegment['mode']; label: string; amapMode: string }> = [
  { value: 'walking', label: '步行', amapMode: 'walk' },
  { value: 'transit', label: '公交', amapMode: 'bus' },
  { value: 'driving', label: '驾车', amapMode: 'car' },
]

const modeLabel = computed(() => routeModes.find((item) => item.value === activeRoute.value.mode)?.label || '路线')

const navigationUrl = computed(() => {
  const route = activeRoute.value
  if (
    route.origin_longitude === null || route.origin_longitude === undefined
    || route.origin_latitude === null || route.origin_latitude === undefined
    || route.destination_longitude === null || route.destination_longitude === undefined
    || route.destination_latitude === null || route.destination_latitude === undefined
  ) return ''
  const url = new URL('https://uri.amap.com/navigation')
  url.searchParams.set('from', `${route.origin_longitude},${route.origin_latitude},${route.origin_name}`)
  url.searchParams.set('to', `${route.destination_longitude},${route.destination_latitude},${route.destination_name}`)
  url.searchParams.set('mode', routeModes.find((item) => item.value === route.mode)?.amapMode || 'bus')
  url.searchParams.set('src', 'travel-agent')
  url.searchParams.set('callnative', '1')
  return url.toString()
})

const jsKey = import.meta.env.VITE_AMAP_JS_KEY?.trim() || ''

function coordinates(route: RouteSegment = activeRoute.value) {
  if (
    route.origin_longitude === null || route.origin_longitude === undefined
    || route.origin_latitude === null || route.origin_latitude === undefined
    || route.destination_longitude === null || route.destination_longitude === undefined
    || route.destination_latitude === null || route.destination_latitude === undefined
  ) return [] as Array<[number, number]>
  return [
    [route.origin_longitude, route.origin_latitude],
    [route.destination_longitude, route.destination_latitude],
  ] as Array<[number, number]>
}

function destroyMap() {
  if (resizeTimer !== null) {
    window.clearTimeout(resizeTimer)
    resizeTimer = null
  }
  if (mapInstance) mapInstance.destroy()
  mapInstance = null
  mapReady.value = false
}

function resizeMap() {
  if (mapInstance && typeof mapInstance.resize === 'function') {
    mapInstance.resize()
  }
}

function scheduleMapResize() {
  resizeMap()
  window.requestAnimationFrame(() => resizeMap())
  resizeTimer = window.setTimeout(() => {
    resizeTimer = null
    resizeMap()
  }, 120)
}

async function renderMap() {
  const version = ++renderVersion
  const route = activeRoute.value
  destroyMap()
  mapMessage.value = ''
  if (!jsKey) {
    mapMessage.value = '高德地图 JS Key 尚未配置，当前显示路线示意'
    return
  }
  if (coordinates(route).length < 2) {
    mapMessage.value = '当前路线缺少坐标，暂时显示路线信息'
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
    })
    mapInstance = map
    const overlays: any[] = []
    const markerData = [
      [route.origin_longitude, route.origin_latitude, route.origin_name, '1'],
      [route.destination_longitude, route.destination_latitude, route.destination_name, '2'],
    ]
    markerData.forEach(([longitude, latitude, title, label]) => {
      const marker = new AMap.Marker({
        position: [longitude, latitude],
        anchor: 'center',
        content: `<span class="general-route-marker">${label}</span>`,
        title,
      })
      overlays.push(marker)
      map.add(marker)
    })
    const resolved = await resolveAmapRoutePath(AMap, route)
    if (version !== renderVersion || !mapElement.value) return
    const { path, hasRealGeometry } = resolved
    if (path.length >= 2) {
      const line = new AMap.Polyline({
        path,
        strokeColor: '#2f7c73',
        strokeWeight: 6,
        strokeOpacity: 0.86,
        strokeStyle: hasRealGeometry && !route.estimated ? 'solid' : 'dashed',
        lineJoin: 'round',
        lineCap: 'round',
      })
      overlays.push(line)
      map.add(line)
    }
    map.setFitView(overlays, false, [44, 44, 44, 44], 16)
    mapReady.value = true
    await nextTick()
    scheduleMapResize()
  } catch (error) {
    mapMessage.value = error instanceof Error ? error.message : '高德路线地图加载失败'
  }
}

async function selectMode(mode: RouteSegment['mode']) {
  if (mode === activeRoute.value.mode || modeLoading.value) return
  modeError.value = ''
  if (!jsKey) {
    modeError.value = '暂时无法切换路线方式，请先配置高德地图。'
    return
  }
  const requestVersion = ++modeRequestVersion
  modeLoading.value = true
  try {
    const AMap = await loadAmap()
    const result = await fetchAmapRouteDetails(AMap, { ...props.route, mode, polyline: [] })
    if (requestVersion !== modeRequestVersion) return
    const hasRouteSummary = result.distance_m !== null && result.duration_min !== null
    const nextRoute: RouteSegment = {
      ...props.route,
      mode,
      distance_m: result.distance_m,
      duration_min: result.duration_min,
      source: hasRouteSummary ? 'amap' : 'unavailable',
      estimated: false,
      polyline: result.path,
    }
    activeRoute.value = nextRoute
    modeError.value = hasRouteSummary ? '' : '这个方式暂时没有查到路线，可以换一种方式或打开高德查看。'
    emit('update:route', nextRoute)
    await renderMap()
  } catch (error) {
    if (requestVersion === modeRequestVersion) {
      modeError.value = error instanceof Error ? error.message : '路线切换失败，请稍后重试。'
    }
  } finally {
    if (requestVersion === modeRequestVersion) modeLoading.value = false
  }
}

function formatDistance(value: number | null) {
  if (value === null) return '距离待定'
  return value < 1000 ? `${value}米` : `${(value / 1000).toFixed(1)}公里`
}

watch(
  () => props.route,
  (route) => {
    activeRoute.value = route
    void renderMap()
  },
  { deep: true, immediate: true },
)
onBeforeUnmount(() => {
  renderVersion += 1
  modeRequestVersion += 1
  destroyMap()
})
</script>

<template>
  <section class="general-route-card" aria-label="路线地图卡片">
    <header class="general-route-card-header">
      <div>
        <span>ROUTE MAP</span>
        <strong>路线地图</strong>
      </div>
      <small>当天移动路线</small>
    </header>
    <div class="general-route-path">
      <div><i>1</i><strong>{{ activeRoute.origin_name }}</strong></div>
      <span></span>
      <div><i>2</i><strong>{{ activeRoute.destination_name }}</strong></div>
    </div>
    <div class="general-route-stats">
      <span>{{ modeLabel }}</span>
      <strong>{{ formatDistance(activeRoute.distance_m) }}</strong>
      <strong>{{ activeRoute.duration_min === null ? '耗时待定' : `${activeRoute.duration_min}分钟` }}</strong>
    </div>
    <div class="general-route-actions">
      <div class="general-route-modes" role="group" aria-label="切换路线方式">
        <button
          v-for="item in routeModes"
          :key="item.value"
          type="button"
          :class="{ active: activeRoute.mode === item.value }"
          :aria-pressed="activeRoute.mode === item.value"
          :disabled="modeLoading || disabled"
          @click="selectMode(item.value)"
        >
          {{ item.label }}
        </button>
      </div>
      <a
        v-if="navigationUrl"
        class="general-route-navigation"
        :href="navigationUrl"
        target="_blank"
        rel="noopener noreferrer"
      >
        打开高德导航 <span aria-hidden="true">↗</span>
      </a>
    </div>
    <p v-if="modeError" class="general-route-mode-error" role="status">{{ modeError }}</p>
    <p v-if="modeLoading" class="general-route-mode-loading" role="status">正在重新查询路线…</p>
    <div ref="mapElement" class="general-route-map-canvas" :class="{ ready: mapReady }" :aria-busy="modeLoading"></div>
    <div v-if="!mapReady" class="general-route-fallback">
      <div class="general-route-fallback-line"><i>1</i><span></span><i>2</i></div>
      <p>{{ mapMessage || '正在加载高德路线地图…' }}</p>
    </div>
  </section>
</template>
