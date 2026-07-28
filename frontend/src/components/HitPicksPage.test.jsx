import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import HitPicksPage from './HitPicksPage'

function response(data, ok = true) {
  return {
    ok,
    status: ok ? 200 : 404,
    json: async () => (ok ? { code: 200, data } : { detail: 'Not found' }),
  }
}

function pick(overrides = {}) {
  return {
    rank: 1,
    player_id: 1,
    player_name: 'Current Player',
    team: 'Detroit Tigers',
    batting_order: 1,
    bats: 'L',
    hit_probability: 0.72,
    last10_hit_per_pa: 0.31,
    pitcher_name: 'Right Hander',
    pitcher_throws: 'R',
    platoon_advantage: 1,
    played: null,
    got_hit: null,
    ...overrides,
  }
}

describe('HitPicksPage history', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('loads a past calendar date and displays its final batting line', async () => {
    const latest = {
      date: '2026-07-05',
      model_version: 'hit_gbm_v2',
      trained_on_rows: 150000,
      grading_status: 'pending',
      available_models: [{ model_version: 'hit_gbm_v2', is_public: true }],
      picks: [pick()],
    }
    const historical = {
      date: '2026-07-04',
      model_version: 'hit_gbm_v2',
      trained_on_rows: 149000,
      grading_status: 'graded',
      available_models: [{ model_version: 'hit_gbm_v2', is_public: true }],
      picks: [pick({
        player_name: 'History Player',
        played: 1,
        got_hit: 1,
        hits: 2,
        at_bats: 4,
        plate_appearances: 5,
        doubles: 1,
        triples: 0,
        home_runs: 1,
        runs: 2,
        rbi: 3,
        walks: 1,
        strikeouts: 1,
        total_bases: 6,
      })],
    }

    const fetchMock = vi.fn(async (url) => {
      if (url === '/hit-picks/latest?top=15') return response(latest)
      if (url === '/hit-picks/dates?limit=365') {
        return response({
          latest_date: '2026-07-05',
          count: 2,
          dates: [
            { date: '2026-07-05', grading_status: 'pending' },
            { date: '2026-07-04', grading_status: 'graded' },
          ],
        })
      }
      if (url === '/hit-picks/ledger') {
        return response({ summary: {}, days_graded: 0 })
      }
      if (url === '/hit-picks/2026-07-04?top=15') return response(historical)
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<HitPicksPage />)

    expect(await screen.findByText('Current Player (L)')).toBeInTheDocument()
    expect(screen.getByText('Awaiting final box score')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', {
      name: 'Show picks for 2026-07-04, graded',
    }))

    expect(await screen.findByText('History Player (L)')).toBeInTheDocument()
    expect(screen.getByText('2-for-4, 1 2B, 1 HR, 2 R, 3 RBI, 1 BB, 1 K, 6 TB')).toBeInTheDocument()
    expect(screen.getByText('Hit')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/hit-picks/2026-07-04?top=15')
  })

  it('switches between public and shadow models for the selected date', async () => {
    const publicBoard = {
      date: '2026-07-05',
      model_version: 'hit_gbm_v2',
      trained_on_rows: 150000,
      grading_status: 'graded',
      available_models: [
        { model_version: 'hit_gbm_v2', is_public: true },
        { model_version: 'hit_gbm_v3', is_public: false },
      ],
      picks: [pick({ played: 1, got_hit: 0, hits: 0, at_bats: 4 })],
    }
    const shadowBoard = {
      ...publicBoard,
      model_version: 'hit_gbm_v3',
      picks: [pick({
        player_id: 3,
        player_name: 'V3 Player',
        played: 1,
        got_hit: 1,
        hits: 1,
        at_bats: 3,
      })],
    }

    const fetchMock = vi.fn(async (url) => {
      if (url === '/hit-picks/latest?top=15') return response(publicBoard)
      if (url === '/hit-picks/dates?limit=365') {
        return response({
          latest_date: '2026-07-05',
          dates: [{ date: '2026-07-05', grading_status: 'graded' }],
        })
      }
      if (url === '/hit-picks/ledger') return response({ summary: {} })
      if (url.includes('model_version=hit_gbm_v3')) return response(shadowBoard)
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<HitPicksPage />)
    const selector = await screen.findByLabelText('Model')
    fireEvent.change(selector, { target: { value: 'hit_gbm_v3' } })

    await waitFor(() => {
      expect(screen.getByText('V3 Player (L)')).toBeInTheDocument()
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/hit-picks/2026-07-05?top=15&model_version=hit_gbm_v3',
    )
  })
})
