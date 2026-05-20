import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import BettingPage from './BettingPage'

describe('BettingPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders the no-vig edge formula on the betting candidate page', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        code: 200,
        data: {
          date: '2026-05-20',
          generated_at: '2026-05-20T12:00:00Z',
          park_factor_meta: { source: 'static_fallback' },
          thresholds: {
            min_composite_score: 3,
            min_fired_signals: 2,
          },
          candidates: [],
        },
      }),
    })

    vi.stubGlobal('fetch', fetchMock)

    render(<BettingPage onBack={() => {}} />)

    await waitFor(() => {
      expect(screen.getByText('model probability - no-vig market probability = edge')).toBeInTheDocument()
    })

    expect(fetchMock).toHaveBeenCalledWith('/betting/candidates')
    expect(screen.getByText('Edge formula')).toBeInTheDocument()
    expect(screen.getByText(/Positive edge means our projection is stronger/)).toBeInTheDocument()
  })
})
