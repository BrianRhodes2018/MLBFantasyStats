import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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

  it('separates before-first-pitch picks from started games', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        code: 200,
        data: {
          date: '2026-05-21',
          generated_at: '2026-05-21T12:00:00Z',
          park_factor_meta: { source: 'static_fallback' },
          thresholds: {
            min_composite_score: 50,
            min_fired_signals: 3,
          },
          candidates: [
            {
              rank: 1,
              player_mlb_id: 1,
              player_name: 'Started Game Bat',
              player_team: 'Detroit Tigers',
              game_id: 100,
              opposing_pitcher_name: 'Final Pitcher',
              venue: 'Comerica Park',
              game_time: '2026-05-21T17:10:00Z',
              game_status: 'Final',
              composite_score: 78.5,
              signals: {},
              summary: 'Started game pick.',
            },
            {
              rank: 2,
              player_mlb_id: 2,
              player_name: 'Pregame Bat',
              player_team: 'New York Yankees',
              game_id: 200,
              opposing_pitcher_name: 'Pregame Pitcher',
              venue: 'Yankee Stadium',
              game_time: '2026-05-21T23:05:00Z',
              game_status: 'Pre-Game',
              composite_score: 67.4,
              signals: {},
              summary: 'Pregame pick.',
            },
          ],
        },
      }),
    })

    vi.stubGlobal('fetch', fetchMock)

    render(<BettingPage onBack={() => {}} />)

    await waitFor(() => {
      expect(screen.getByText('Pregame Bat')).toBeInTheDocument()
    })

    expect(screen.queryByText('Started Game Bat')).not.toBeInTheDocument()
    expect(screen.getByText(/1 before first pitch/)).toBeInTheDocument()

    const toggle = screen.getByLabelText(/Show 1 pick from 1 game already started or final/)
    fireEvent.click(toggle)

    expect(screen.getByText('Started Game Bat')).toBeInTheDocument()
  })
})
