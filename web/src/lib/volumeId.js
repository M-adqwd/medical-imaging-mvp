/**
 * 体数据 ID 的唯一口径。
 *
 * 为什么不能只用 seriesUid：cornerstone 的体数据对象以 volumeId 为键存在**全局**
 * 缓存（core.cache）里，与组件生命周期无关。同一序列二次进入时若复用同一个 ID，
 * 可能命中上一次遗留、且停在 `loadStatus.loading === true` 的对象；而
 * BaseStreamingImageVolume.load() 见到 loading 为真会直接 return —— 既不发起
 * WADO 请求也不回调，界面就永远停在「灌入体数据 N 层」。医生侧看到的现象
 * 就是"图像有时候显示不出来"（切换病例 / 反复进出后更容易触发）。
 *
 * 因此由工作台按"进入病例 / 切换序列"自增一个 tag，MPR 与 VR 都从这里取 ID：
 * 每次都是全新对象，从根上避开脏状态命中；两者仍共用同一个 ID，不会重复下载。
 */
export function makeVolumeId(seriesUid, tag = 'v1') {
  return `cornerstoneStreamingImageVolume:${tag}:${seriesUid}`
}

// 模块级自增：跨组件挂载也单调递增，所以"退出病例再进入同一序列"同样拿到新 tag，
// 不会命中上一次遗留的体数据对象（那个对象的 loadStatus 可能永远是 loading）。
let seq = 0

export function nextVolumeTag() {
  seq += 1
  return `v${seq}`
}
