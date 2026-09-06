// Minimal service worker — required by Chrome/Android for the "Add to Home
// screen" install prompt to produce a standalone (no browser chrome) app.
// No caching; every request just passes straight through to the network.
self.addEventListener('fetch', () => {});
