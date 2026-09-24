import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(cleanup)

Object.defineProperty(window.navigator, 'clipboard', {
  configurable: true,
  value: { writeText: vi.fn() },
})
