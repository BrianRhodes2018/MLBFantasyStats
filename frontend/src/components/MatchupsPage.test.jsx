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
                    throws: 'L',
                    career_stats: { wins: 10, losses: 8, era: '3.75', fip: 3.91, whip: '1.21', strikeouts: 120, innings_pitched: '150.0' },
                    season_stats: { wins: 3, losses: 2, era: '3.20', xera: 3.44, fip: 3.55, xfip: 3.70, whip: '1.09', strikeouts: 51, innings_pitched: '50.0' },
                    rolling_stats: {
                      30: { wins: 1, losses: 1, era: '4.20', xera: '-', fip: '4.05', xfip: '-', whip: '1.22', strikeouts: 20, innings_pitched: '19.1' },
                      45: { wins: 2, losses: 1, era: '3.95', xera: '-', fip: '3.90', xfip: '-', whip: '1.18', strikeouts: 31, innings_pitched: '27.0' },
                      60: { wins: 3, losses: 1, era: '3.70', xera: '-', fip: '3.75', xfip: '-', whip: '1.15', strikeouts: 42, innings_pitched: '36.2' },
                    },
                  },
                  home_pitcher: {
                    mlb_id: 2,
                    name: 'Home Arm',
                    throws: 'R',
                    career_stats: { wins: 12, losses: 9, era: '4.10', fip: 4.22, whip: '1.30', strikeouts: 140, innings_pitched: '160.0' },
                    season_stats: { wins: 4, losses: 1, era: '2.95', xera: 3.05, fip: 3.18, xfip: 3.40, whip: '1.02', strikeouts: 60, innings_pitched: '55.0' },
                    rolling_stats: {
                      30: { wins: 2, losses: 0, era: '2.80', xera: '-', fip: '3.00', xfip: '-', whip: '0.98', strikeouts: 28, innings_pitched: '22.0' },
                      45: { wins: 3, losses: 0, era: '2.90', xera: '-', fip: '3.06', xfip: '-', whip: '1.00', strikeouts: 39, innings_pitched: '32.0' },
                      60: { wins: 4, losses: 1, era: '3.05', xera: '-', fip: '3.20', xfip: '-', whip: '1.05', strikeouts: 52, innings_pitched: '43.0' },
                    },
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

      if (url === '/matchups/lineup-stats/100?range=5day&batter_ids=99') {
        return {
          ok: true,
          json: async () => ({
            code: 200,
            data: {
              range: '5day',
              stats: {
                99: { avg: '.300', home_runs: 1, rbi: 3, obp: '.400', ops: '.900', strikeouts: 2, at_bats: 10 },
              },
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
    expect(screen.getByText('Away Arm (LHP)')).toBeInTheDocument()
    expect(screen.getByText('Home Arm (RHP)')).toBeInTheDocument()
    expect(screen.getByText('30 Day')).toBeInTheDocument()
    expect(screen.getByText('45 Day')).toBeInTheDocument()
    expect(screen.getByText('60 Day')).toBeInTheDocument()

    fireEvent.click(screen.getByText('30 Day'))

    expect(screen.getAllByText('Last 30 Days').length).toBeGreaterThan(0)
    expect(screen.getByText('4.20')).toBeInTheDocument()

    fireEvent.click(screen.getByText('@').closest('.game-card-header'))

    await waitFor(() => {
      expect(screen.getByText('Projected Bat (L)')).toBeInTheDocument()
    })

    expect(screen.getByText('Projected')).toBeInTheDocument()
    expect(screen.getByText(/14-day lookback/)).toBeInTheDocument()
    expect(screen.getByText(/conf 75%/)).toBeInTheDocument()
    expect(screen.getByText('5 Day')).toBeInTheDocument()

    fireEvent.click(screen.getByText('5 Day'))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/matchups/lineup-stats/100?range=5day&batter_ids=99')
    })
    await waitFor(() => {
      expect(screen.getByText('.300')).toBeInTheDocument()
    })
  })
})
