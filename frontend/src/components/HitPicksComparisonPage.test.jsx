import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import HitPicksComparisonPage from './HitPicksComparisonPage'

function response(data) {
  return {
    ok: true,
    status: 200,
    json: async () => ({ code: 200, data }),
  }
}

describe('HitPicksComparisonPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('does not compare an unpaired V2 board to arbitrary V3 output', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url === '/hit-picks/compare/latest?top=15') {
        return response({
          date: '2026-07-05',
          comparable: false,
          reason: 'No visible V3 run was scored from this V2 snapshot.',
        })
      }
      if (url === '/hit-picks/boards/primary/dates?limit=365') {
        return response({
          dates: [{ date: '2026-07-05', grading_status: 'pending' }],
        })
      }
      throw new Error(`Unexpected URL: ${url}`)
    }))

    render(<HitPicksComparisonPage />)
    expect(await screen.findByText('No paired V3 run for this snapshot')).toBeInTheDocument()
    expect(screen.getByText(
      'No visible V3 run was scored from this V2 snapshot.',
    )).toBeInTheDocument()
  })

  it('renders paired ranks, scores, and the actual statline', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url === '/hit-picks/compare/latest?top=15') {
        return response({
          date: '2026-07-05',
          prediction_window: 'afternoon',
          comparable: true,
          coverage: {
            shared_candidates: 288,
            primary_candidates: 288,
            challenger_fraction: 1,
          },
          rows: [{
            game_pk: 777,
            player_id: 1,
            player_name: 'Paired Player',
            team: 'DET',
            primary_rank: 3,
            primary_score: 0.68,
            challenger_rank: 1,
            challenger_score: 0.74,
            score_delta: 0.06,
            rank_movement: 2,
            lineup_source: 'confirmed',
            pitcher_name: 'Opposing Pitcher',
            played: 1,
            got_hit: 1,
            hits: 2,
            at_bats: 4,
            plate_appearances: 4,
            doubles: 1,
            triples: 0,
            home_runs: 0,
            runs: 1,
            rbi: 1,
            walks: 0,
            strikeouts: 1,
            total_bases: 3,
          }],
        })
      }
      if (url === '/hit-picks/boards/primary/dates?limit=365') {
        return response({
          dates: [{ date: '2026-07-05', grading_status: 'graded' }],
        })
      }
      throw new Error(`Unexpected URL: ${url}`)
    }))

    render(<HitPicksComparisonPage />)
    expect(await screen.findByText('Paired Player')).toBeInTheDocument()
    expect(screen.getByText('▲ 2')).toBeInTheDocument()
    expect(screen.getByText('2-for-4, 1 2B, 1 R, 1 RBI, 1 K, 3 TB')).toBeInTheDocument()
  })
})
