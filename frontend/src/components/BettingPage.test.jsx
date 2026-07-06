import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import BettingPage from './BettingPage'

describe('BettingPage', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  function mockBettingFetch(edgeData, hitData = null) {
    const hitPayload = hitData ?? {
      date: edgeData.date,
      generated_at: edgeData.generated_at,
      thresholds: { hit_form_days: 5 },
      candidates: [],
    }
    return vi.fn(async (url) => {
      const data = String(url).includes('/betting/hit-candidates') ? hitPayload : edgeData
      return {
        ok: true,
        json: async () => ({
          code: 200,
          data,
        }),
      }
    })
  }

  it('renders the no-vig edge formula on the betting candidate page', async () => {
    const fetchMock = mockBettingFetch({
      date: '2026-05-20',
      generated_at: '2026-05-20T12:00:00Z',
      park_factor_meta: { source: 'static_fallback' },
      thresholds: {
        min_composite_score: 3,
        min_fired_signals: 2,
      },
      candidates: [],
    })

    vi.stubGlobal('fetch', fetchMock)

    render(<BettingPage onBack={() => {}} />)

    await waitFor(() => {
      expect(screen.getByText('model probability - no-vig market probability = edge')).toBeInTheDocument()
    })

    expect(fetchMock).toHaveBeenCalledWith('/betting/candidates')
    expect(fetchMock).toHaveBeenCalledWith('/betting/hit-candidates')
    expect(screen.getByText('Edge formula')).toBeInTheDocument()
    expect(screen.getByText(/Positive edge means our projection is stronger/)).toBeInTheDocument()
  })

  it('renders the separate hit candidate board', async () => {
    const fetchMock = mockBettingFetch(
      {
        date: '2026-05-22',
        generated_at: '2026-05-22T12:00:00Z',
        park_factor_meta: { source: 'static_fallback' },
        thresholds: {
          min_composite_score: 50,
          min_fired_signals: 3,
        },
        candidates: [],
      },
      {
        date: '2026-05-22',
        generated_at: '2026-05-22T12:00:00Z',
        thresholds: { hit_form_days: 5 },
        candidates: [
          {
            hit_rank: 1,
            rank: 12,
            player_mlb_id: 42,
            player_name: 'Contact Bat',
            bats: 'L',
            player_team: 'Boston Red Sox',
            game_id: 500,
            opposing_pitcher_name: 'Contact Pitcher',
            opposing_pitcher_throws: 'R',
            venue: 'Fenway Park',
            game_time: '2026-05-22T23:10:00Z',
            game_status: 'Pre-Game',
            batting_order: 2,
            lineup_source: 'confirmed',
            hit_candidate: {
              score: 77.2,
              hit_confidence: 0.772,
              hit_probability: 0.682,
              per_pa_hit_probability: 0.242,
              expected_pa: 4.55,
              form_signal: { value: 0.82, window_days: 5 },
              reasons: ['top-half lineup slot', 'short-window form'],
              risks: [],
            },
            context_stats: {
              hit_form_days: 5,
              rolling_hit_rate_per_pa: 0.31,
              season_hit_rate_per_pa: 0.24,
              hit_rolling_k_pct: 15.5,
            },
          },
        ],
      },
    )

    vi.stubGlobal('fetch', fetchMock)

    render(<BettingPage onBack={() => {}} />)

    await waitFor(() => {
      expect(screen.getByText('Contact Bat (L)')).toBeInTheDocument()
    })

    expect(screen.getByText("Today's Hit Candidates")).toBeInTheDocument()
    expect(screen.getByText(/ranked by 1\+ hit confidence/)).toBeInTheDocument()
    expect(screen.getByText('77%')).toBeInTheDocument()
    expect(screen.getByText('68%')).toBeInTheDocument()
    expect(screen.getByText('Contact Pitcher (RHP)')).toBeInTheDocument()
    expect(screen.getByText('short-window form')).toBeInTheDocument()
    expect(screen.getByText('31%')).toBeInTheDocument()
  })

  it('separates before-first-pitch picks from started games', async () => {
    const fetchMock = mockBettingFetch({
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
              bats: 'R',
              player_team: 'Detroit Tigers',
              game_id: 100,
              opposing_pitcher_name: 'Final Pitcher',
              opposing_pitcher_throws: 'L',
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
              bats: 'L',
              player_team: 'New York Yankees',
              game_id: 200,
              opposing_pitcher_name: 'Pregame Pitcher',
              opposing_pitcher_throws: 'R',
              venue: 'Yankee Stadium',
              game_time: '2026-05-21T23:05:00Z',
              game_status: 'Pre-Game',
              composite_score: 67.4,
              signals: {},
              summary: 'Pregame pick.',
            },
          ],
    })

    vi.stubGlobal('fetch', fetchMock)

    render(<BettingPage onBack={() => {}} />)

    await waitFor(() => {
      expect(screen.getByText('Pregame Bat (L)')).toBeInTheDocument()
    })

    expect(screen.queryByText('Started Game Bat (R)')).not.toBeInTheDocument()
    expect(screen.getByText('Pregame Pitcher (RHP)')).toBeInTheDocument()
    expect(screen.getByText(/1 before first pitch/)).toBeInTheDocument()

    const toggle = screen.getByLabelText(/Show 1 pick from 1 game already started or final/)
    fireEvent.click(toggle)

    expect(screen.getByText('Started Game Bat (R)')).toBeInTheDocument()
    expect(screen.getByText('Final Pitcher (LHP)')).toBeInTheDocument()
  })

  it('shows projected lineup source and the 8 percent edge floor', async () => {
    const fetchMock = mockBettingFetch({
          date: '2026-05-27',
          generated_at: '2026-05-27T12:00:00Z',
          park_factor_meta: { source: 'baseball_savant', year_range: '2024-2026' },
          thresholds: {
            min_composite_score: 50,
            min_fired_signals: 3,
            projected_lineup_edge_threshold: 0.08,
          },
          lineup_meta: {
            mode: 'hybrid',
            provider: 'mlb_recent_lineups',
            status: 'ok',
            provider_meta: {
              lookback_days: 14,
              confidence_floor: 0.5,
            },
            lineup_counts: { confirmed: 0, projected: 1 },
          },
          candidates: [
            {
              rank: 1,
              player_mlb_id: 99,
              player_name: 'Projected Bat',
              bats: 'S',
              player_team: 'New York Yankees',
              game_id: 300,
              opposing_pitcher_name: 'Projected Pitcher',
              opposing_pitcher_throws: 'R',
              venue: 'Yankee Stadium',
              game_time: '2026-05-27T23:05:00Z',
              game_status: 'Pre-Game',
              composite_score: 66.2,
              batting_order: 2,
              lineup_source: 'projected',
              lineup_provider: 'mlb_recent_lineups',
              lineup_edge_threshold: 0.08,
              lineup_confidence: 0.75,
              lineup_sample_size: 9,
              lineup_games_considered: 12,
              lineup_split: 'all',
              signals: {},
              summary: 'Projected pick.',
              context_stats: {},
            },
          ],
    })

    vi.stubGlobal('fetch', fetchMock)

    render(<BettingPage onBack={() => {}} />)

    await waitFor(() => {
      expect(screen.getByText('Projected Bat (S)')).toBeInTheDocument()
    })

    expect(screen.getByText('Projected Pitcher (RHP)')).toBeInTheDocument()
    expect(screen.getByText(/0 confirmed \/ 1 projected/)).toBeInTheDocument()
    expect(screen.getByText('Projected')).toBeInTheDocument()
    expect(screen.getByText('batting #2')).toBeInTheDocument()
    expect(screen.getAllByText('8%').length).toBeGreaterThan(0)
    expect(screen.getByText(/14-day lookback/)).toBeInTheDocument()
    expect(screen.getByText('lineup conf:')).toBeInTheDocument()
    expect(screen.getByText('75%')).toBeInTheDocument()
  })
})
