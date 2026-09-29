<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { NearbyPlaceResult, NearbySearchResult } from '../types'
import { loadAmap } from '../utils/amapLoader'

const props = defineProps<{ nearby: NearbySearchResult }>()

const mapElement = ref<HTMLElement | null>(null)
const mapMessage = ref('')
const jsKey = import.meta.env.VITE_AMAP_JS_KEY?.trim() || ''
let mapInstance: any = null
let renderVersion = 0

function validPosition(place: Pick<NearbyPlaceResult, 'longitude' | 'latitude'>) {
  return Number.isFinite(place.longitude) && Number.isFinite(place.latitude)
    && place.longitude !== 0 && place.latitude !== 0
}

function destroyMap() {
  if (mapInstance) mapInstance.destroy()
  mapInstance = null
}

async function renderMap() {
  const version = ++renderVersion
  destroyMap()
  mapMessage.value = ''
  if (!jsKey) {
    mapMessage.value = '地图暂不可用，仍可查看地点并打开高德导航'
    return
  }
  await nextTick()
  if (!mapElement.value) return
  try {
    const AMap = await loadAmap()
    if (version !== renderVersion || !mapElement.value) return
    const map = new AMap.Map(mapElement.value, {
      viewMode: '2D',
      zoom: 15,
      mapStyle: 'amap://styles/light',
    })
    mapInstance = map
    const overlays: any[] = []
    const center = {
      longitude: props.nearby.center_longitude,
      latitude: props.nearby.center_latitude,
    }
    if (validPosition(center)) {
      const marker = new AMap.Marker({
        position: [center.longitude, center.latitude],
        anchor: 'center',
        content: '<span class="nearby-center-marker">中心</span>',
        title: props.nearby.center_name,
      })
      overlays.push(marker)
      map.add(marker)
    }
    props.nearby.places.forEach((place, index) => {
      if (!validPosition(place)) return
      const marker = new AMap.Marker({
        position: [place.longitude, place.latitude],
        anchor: 'center',
        content: `<span class="nearby-place-marker">${index + 1}</span>`,
        title: place.name,
      })
      overlays.push(marker)
      map.add(marker)
    })
    if (overlays.length) {
      map.setFitView(overlays, false, [28, 28, 28, 28], 17)
    } else {
      map.setCenter([center.longitude, center.latitude])
    }
  } catch (error) {
    mapMessage.value = error instanceof Error ? error.message : '地图加载失败，仍可打开高德导航'
  }
}

function distanceLabel(distance: number | null) {
  if (distance === null) return '直线距离未知'
  const value = distance < 1000 ? `${distance}米` : `${(distance / 1000).toFixed(1)}公里`
  return `直线${value}`
}

function navigationUrl(place: NearbyPlaceResult) {
  const url = new URL('https://uri.amap.com/navigation')
  url.searchParams.set('to', `${place.longitude},${place.latitude},${place.name}`)
  url.searchParams.set('mode', 'walk')
  url.searchParams.set('src', 'travel-agent')
  url.searchParams.set('callnative', '1')
  return url.toString()
}

watch(() => props.nearby, () => void renderMap(), { deep: true, immediate: true })
onBeforeUnmount(() => {
  renderVersion += 1
  destroyMap()
})
</script>

<template>
  <section class="nearby-places-card" aria-label="附近地点卡片">
    <header class="nearby-card-header">
      <div>
        <span>NEARBY PLACES</span>
        <strong>{{ nearby.center_name }}附近 · {{ nearby.category }}</strong>
      </div>
      <small>{{ (nearby.radius_m / 1000).toFixed(1) }} 公里范围</small>
    </header>
    <div class="nearby-map-wrap">
      <div ref="mapElement" class="nearby-map-canvas"></div>
      <div v-if="mapMessage" class="nearby-map-fallback">{{ mapMessage }}</div>
    </div>
    <ol class="nearby-place-list">
      <li v-for="(place, index) in nearby.places" :key="place.poi_id || `${place.name}-${index}`">
        <span class="nearby-place-index">{{ index + 1 }}</span>
        <div class="nearby-place-detail">
          <strong>{{ place.name }}</strong>
          <small>{{ place.address || '地址暂缺' }}</small>
        </div>
        <div class="nearby-place-action">
          <span>{{ distanceLabel(place.distance_m) }}</span>
          <a :href="navigationUrl(place)" target="_blank" rel="noopener noreferrer">导航</a>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.nearby-places-card {
  width: min(100%, 620px);
  overflow: hidden;
  border: 1px solid #d7e6df;
  border-radius: 20px;
  background: #f5f9f6;
  box-shadow: 0 9px 24px rgba(38, 79, 65, .08);
}
.nearby-card-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  padding: 15px 17px 11px;
}
.nearby-card-header div { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.nearby-card-header span { color: #df7c43; font-size: 9px; letter-spacing: .18em; }
.nearby-card-header strong { overflow: hidden; color: #214f49; font-size: 16px; text-overflow: ellipsis; white-space: nowrap; }
.nearby-card-header small { flex: none; color: #82958e; font-size: 11px; }
.nearby-map-wrap { position: relative; margin: 0 12px; overflow: hidden; border-radius: 13px; background: #eaf1ed; }
.nearby-map-canvas { width: 100%; height: 220px; }
.nearby-map-fallback {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 20px;
  color: #82958e;
  background: linear-gradient(135deg, #edf4f0, #e3ece7);
  font-size: 13px;
  text-align: center;
}
.nearby-place-list { display: flex; flex-direction: column; gap: 0; margin: 0; padding: 4px 17px 9px; list-style: none; }
.nearby-place-list li { display: flex; align-items: center; gap: 11px; min-width: 0; padding: 11px 0; border-bottom: 1px solid #e0eae4; }
.nearby-place-list li:last-child { border-bottom: 0; }
.nearby-place-index { display: grid; flex: none; width: 24px; height: 24px; place-items: center; border-radius: 50%; color: white; background: #147b70; font-size: 12px; font-weight: 700; }
.nearby-place-detail { display: flex; flex: 1; flex-direction: column; gap: 4px; min-width: 0; }
.nearby-place-detail strong { overflow: hidden; color: #214f49; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.nearby-place-detail small { overflow: hidden; color: #82958e; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.nearby-place-action { display: flex; flex: none; align-items: flex-end; flex-direction: column; gap: 5px; color: #82958e; font-size: 11px; }
.nearby-place-action a { color: #147b70; font-size: 12px; text-decoration: none; }
.nearby-place-action a:hover { text-decoration: underline; }
:global(.nearby-center-marker) { display: block; padding: 5px 8px; border: 2px solid white; border-radius: 12px; color: white; background: #df7c43; box-shadow: 0 2px 8px #0003; font-size: 11px; font-weight: 700; }
:global(.nearby-place-marker) { display: grid; width: 25px; height: 25px; place-items: center; border: 2px solid white; border-radius: 50%; color: white; background: #147b70; box-shadow: 0 2px 8px #0003; font-size: 12px; font-weight: 700; }
@media (max-width: 640px) {
  .nearby-card-header { align-items: flex-start; flex-direction: column; gap: 4px; }
  .nearby-map-canvas { height: 190px; }
  .nearby-place-list { padding-right: 12px; padding-left: 12px; }
}
</style>
