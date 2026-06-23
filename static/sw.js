/* Spark Dating — Service Worker */
const CACHE = 'spark-v1';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
  if (!event.data) return;
  let data;
  try { data = event.data.json(); }
  catch { data = { title: 'Spark', body: event.data.text(), url: '/matches' }; }

  event.waitUntil(
    self.registration.showNotification(data.title || 'Spark', {
      body: data.body || '',
      icon: '/static/icon-192.png',
      badge: '/static/icon-96.png',
      data: { url: data.url || '/matches' },
      vibrate: [200, 100, 200],
      tag: data.tag || 'spark',
      renotify: true,
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/matches';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const c of list) {
        if (c.url.includes(self.location.origin) && 'focus' in c) {
          c.navigate(url);
          return c.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});
