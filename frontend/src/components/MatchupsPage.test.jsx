import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import MatchupsPage from './MatchupsPage'

describe('MatchupsPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows projected lineups and advanced pitcher indicators', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === '/matchups/today') {
        return {
          ok: true,
          json: async () => ({
            code: 200,
            data: {
              date: '05/30/2026',
              games: [
                {
                  game_id: 100,
                  game_time: '2026-05-30T23:05:00Z',
                  status: 'Pre-Game',
                  away_team: 'New York Yankees',
                  home_team: 'Boston Red Sox',
                  venue: 'Fenway Park',
                  away_pitcher: {
                    mlb_id: 1,
                    name: 'Away Arm',
                    career_stats: { wins: 10, losses: 8, era: '3.75', fip: 3.91, whip: '1.21', strikeouts: 120, innings_pitched: '150.0' },
                    season_stats: { wins: 3, losses: 2, era: '3.20', xera: 3.44, fip: 3.55, xfip: 3.70, whip: '1.09', strikeouts: 51, innings_pitched: '50.0' },
                  },
                  home_pitcher: {
                    mlb_id: 2,
                    name: 'Home Arm',
                    career_stats: { wins: 12, losses: 9, era: '4.10', fip: 4.22, whip: '1.30', strikeouts: 140, innings_pitched: '160.0' },
                    season_stats: { wins: 4, losses: 1, era: '2.95', xera: 3.05, fip: 3.18, xfip: 3.40, whip: '1.02', strikeouts: 60, innings_pitched: '55.0' },
                  },
                },
              ],
            },
          }),
        }
      }

      if (url === '/matchups/lineup/100') {
        return {
          ok: true,
          json: async () => ({
            code: 200,
            data: {
              game_id: 100,
              home_pitcher_id: 2,
              away_pitcher_id: 1,
              lineup_projection_meta: {
                provider: 'mlb_recent_lineups',
                status: 'ok',
                provider_meta: { lookback_days: 14 },
              },
              away_lineup_announced: false,
              away_lineup_projected: true,
              away_lineup_source: 'projected',
              away_lineup_provider: 'mlb_recent_lineups',
              away_lineup: [
                {
                  mlb_id: 99,
                  name: 'Projected Bat',
                  position: 'RF',
                  batting_order: 1,
                  bats: 'L',
                  lineup_source: 'projected',
                  lineup_confidence: 0.75,
                  lineup_sample_size: 9,
                  lineup_games_considered: 12,
                  lineup_split: 'all',
                  season_stats: { avg: '.275', home_runs: 8, rbi: 24, obp: '.345', ops: '.820', strikeouts: 40, at_bats: 160 },
                  career_stats: {},
                  woba: 0.355,
                  xwoba: 0.370,
                },
              ],
              home_lineup_announced: false,
              home_lineup_projected: false,
              home_lineup_source: 'unavailable',
              home_lineup_provider: null,
              home_lineup: [],
            },
          }),
        }
      }

      throw new Error(`Unexpected fetch: ${url}`)
    })

    vi.stubGlobal('fetch', fetchMock)
    render(<MatchupsPage season={null} />)

    await waitFor(() => {
      expect(screen.getByText('New York Yankees')).toBeInTheDocument()
    })

    expect(screen.getAllByText('xERA').length).toBeGreaterThan(0)
    expect(screen.getAllByText('xFIP').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByText('@').closest('.game-card-header'))

    await waitFor(() => {
      expect(screen.getByText('Projected Bat (L)')).toBeInTheDocument()
    })

    expect(screen.getByText('Projected')).toBeInTheDocument()
    expect(screen.getByText(/14-day lookback/)).toBeInTheDocument()
    expect(screen.getByText(/conf 75%/)).toBeInTheDocument()
  })
})
