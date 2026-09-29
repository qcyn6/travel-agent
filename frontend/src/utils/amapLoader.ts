declare global {
  interface Window {
    AMap?: any
    _AMapSecurityConfig?: { securityJsCode: string }
  }
}

let amapLoader: Promise<any> | null = null

export function loadAmap() {
  if (window.AMap) return Promise.resolve(window.AMap)
  if (amapLoader) return amapLoader

  const jsKey = import.meta.env.VITE_AMAP_JS_KEY?.trim() || ''
  const securityCode = import.meta.env.VITE_AMAP_SECURITY_CODE?.trim() || ''
  if (!jsKey) return Promise.reject(new Error('未配置高德地图 JS Key'))
  if (securityCode) window._AMapSecurityConfig = { securityJsCode: securityCode }

  amapLoader = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(jsKey)}`
    script.async = true
    script.dataset.amapJsApi = 'true'
    script.onload = () => {
      if (window.AMap) resolve(window.AMap)
      else {
        amapLoader = null
        reject(new Error('高德地图加载失败'))
      }
    }
    script.onerror = () => {
      amapLoader = null
      reject(new Error('高德地图加载失败'))
    }
    document.head.appendChild(script)
  })
  return amapLoader
}
