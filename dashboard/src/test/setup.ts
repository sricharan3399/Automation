import '@testing-library/jest-dom/vitest'

// jsdom has no ResizeObserver; @tanstack/react-virtual expects one.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = globalThis.ResizeObserver ?? (ResizeObserverStub as never)

// Nothing in the test suite should reach the network.
globalThis.fetch =
  globalThis.fetch ??
  ((() => Promise.reject(new Error('fetch is not stubbed in this test'))) as never)
