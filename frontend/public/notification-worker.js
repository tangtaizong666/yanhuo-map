// No cache or background polling: prices, stock and payment responses remain live.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const url = new URL(event.notification.data?.url || '/', self.location.origin);
    if (url.origin !== self.location.origin) return;
    const windows = await self.clients.matchAll({ type: 'window' });
    const matching = windows.find(client => client.url === url.href);
    if (matching) return matching.focus();
    return self.clients.openWindow(url.href);
  })());
});
