import { api } from './api'

function b64ToU8(b64: string): Uint8Array<ArrayBuffer> {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4)
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'))
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

export const pushSupported = () =>
  'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null
  const reg = await navigator.serviceWorker.ready
  return reg.pushManager.getSubscription()
}

export async function enablePush(): Promise<'ok' | 'denied' | 'unsupported' | 'disabled'> {
  if (!pushSupported()) return 'unsupported'
  const { key, enabled } = await api.pushKey()
  if (!enabled || !key) return 'disabled'
  const perm = await Notification.requestPermission()
  if (perm !== 'granted') return 'denied'
  const reg = await navigator.serviceWorker.ready
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToU8(key) }))
  await api.pushSubscribe(sub.toJSON(), navigator.userAgent.slice(0, 80))
  return 'ok'
}

export async function disablePush(): Promise<void> {
  const sub = await currentSubscription()
  if (!sub) return
  await api.pushUnsubscribe(sub.toJSON())
  await sub.unsubscribe()
}
