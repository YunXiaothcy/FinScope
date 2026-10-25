export interface InstallPrompt extends Event {
  prompt(): Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

export async function registerOnlineWorker(
  enabled: boolean,
  secure: boolean,
  container: ServiceWorkerContainer | undefined,
  waiting: (registration: ServiceWorkerRegistration) => void,
) {
  if (!enabled || !secure || !container) return null
  const registration = await container.register('/sw.js', {
    scope: '/',
    updateViaCache: 'none',
  })
  if (registration.waiting) waiting(registration)
  registration.addEventListener('updatefound', () => {
    const worker = registration.installing
    worker?.addEventListener('statechange', () => {
      if (
        worker.state === 'installed' &&
        container.controller &&
        registration.waiting
      )
        waiting(registration)
    })
  })
  return registration
}
