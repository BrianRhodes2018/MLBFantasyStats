import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PitcherKsComparisonPage from './PitcherKsComparisonPage'
import PitcherKsPage from './PitcherKsPage'


const prediction = {
  rank: 1,
  game_pk: 123,
  pitcher_id: 99,
  pitcher_name: 'Test Starter',
  team: 'Home Team',
  opponent: 'Away Team',
  venue: 'Test Park',
  pitcher_throws: 'R',
  lineup_source: 'projected',
  lineup_confidence: 0.82,
  projected_ks: 6.24,
  median_ks: 6,
  p10_ks: 3,
  p90_ks: 9,
  probability_5_plus: 0.72,
  probability_6_plus: 0.55,
  projected_batters_faced: 23.4,
  actual_ks: null,
}


function apiResponse(data) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ data }),
  })
}


function approachBoard(approach) {
  return {
    projection_date: '2026-08-01',
    approach,
    model_version: `pitcher_ks_v1_${approach}`,
    as_of_timestamp: '2026-08-01T15:00:00+00:00',
    trained_through: '2026-07-31',
    trained_on_rows: 8000,
    backtest: {
      starts: 3000,
      mae: 1.82,
      rmse: 2.27,
      bias: -0.03,
      interval_80_coverage: 0.79,
    },
    predictions: [prediction],
  }
}


describe('Pitcher Ks model pages', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it.each([
    ['decomposed', 'Decomposed Matchup + Workload Simulation'],
    ['count', 'Direct Count + Quantile Model'],
    ['bayes', 'Empirical-Bayes Matchup Baseline'],
  ])('loads and renders the %s approach', async (approach, title) => {
    const fetchMock = vi.fn(() => apiResponse(approachBoard(approach)))
    vi.stubGlobal('fetch', fetchMock)

    render(<PitcherKsPage approach={approach} />)

    expect(screen.getByRole('heading', { name: title })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Test Starter')).toBeInTheDocument())
    expect(screen.getByText('72.0%')).toBeInTheDocument()
    expect(screen.getByText(/3,000 held-out starts/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/api/pitcher-ks/approaches/${approach}/latest`),
    )
  })

  it('loads the strict paired comparison board', async () => {
    const comparison = {
      projection_date: '2026-08-01',
      prediction_window: 'afternoon',
      as_of_timestamp: '2026-08-01T15:00:00+00:00',
      candidate_cohort_id: 'abcdef1234567890',
      rows: [{
        ...prediction,
        model_spread: 0.7,
        decomposed: { projected_ks: 6.4, projected_batters_faced: 23.2 },
        count: { projected_ks: 6.8, projected_batters_faced: 23.0 },
        bayes: { projected_ks: 6.1, projected_batters_faced: 22.8 },
      }],
    }
    const fetchMock = vi.fn(() => apiResponse(comparison))
    vi.stubGlobal('fetch', fetchMock)

    render(<PitcherKsComparisonPage />)

    await waitFor(() => expect(screen.getByText('Test Starter')).toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'Pitcher Ks Model Comparison' })).toBeInTheDocument()
    expect(screen.getByText('0.70')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/pitcher-ks/compare/latest'),
    )
  })

  it('shows confirmed results, signed error, history, and the shared glossary', async () => {
    const gradedBoard = {
      ...approachBoard('decomposed'),
      evaluation: {
        complete: true,
        graded_starts: 1,
        mae: 1.24,
      },
      predictions: [{
        ...prediction,
        actual_ks: 5,
        actual_batters_faced: 22,
        actual_innings_pitched: 5.2,
        error: 1.24,
        result_status: 'graded',
        grade_detail: 'Confirmed from the official final MLB pitching line.',
      }],
    }
    const fetchMock = vi.fn((url) => {
      if (url.includes('/dates')) {
        return apiResponse({
          dates: [{ date: '2026-08-01', grading_status: 'graded' }],
        })
      }
      if (url.includes('/ledger')) {
        return apiResponse({
          approaches: {
            decomposed: {
              graded_starts: 1,
              days: 1,
              mae: 1.24,
              rmse: 1.24,
              bias: 1.24,
            },
          },
        })
      }
      return apiResponse(gradedBoard)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<PitcherKsPage approach="decomposed" />)

    await waitFor(() => expect(screen.getByText('+1.24')).toBeInTheDocument())
    expect(screen.getByText('Final')).toBeInTheDocument()
    expect(screen.getByLabelText('Pitcher Ks projection history calendar')).toBeInTheDocument()

    fireEvent.click(screen.getByText('How to read this board'))
    expect(screen.getByText('Mean absolute error: the average distance between projected and actual Ks. Lower is better.')).toBeInTheDocument()
    expect(screen.getByText(/An 80% prediction interval/)).toBeInTheDocument()
  })

  it('shows per-model errors and the closest approach on a graded comparison', async () => {
    const comparison = {
      projection_date: '2026-08-01',
      prediction_window: 'afternoon',
      as_of_timestamp: '2026-08-01T15:00:00+00:00',
      candidate_cohort_id: 'abcdef1234567890',
      evaluation: {
        complete: true,
        graded_starters: 1,
        best_approaches: ['decomposed'],
        approaches: {
          decomposed: { mae: 0.4, rmse: 0.4, bias: 0.4 },
          count: { mae: 0.8, rmse: 0.8, bias: 0.8 },
          bayes: { mae: 0.9, rmse: 0.9, bias: 0.9 },
        },
      },
      rows: [{
        ...prediction,
        actual_ks: 6,
        actual_batters_faced: 23,
        actual_innings_pitched: 6,
        result_status: 'graded',
        closest_approaches: ['decomposed'],
        model_spread: 0.7,
        decomposed: { projected_ks: 6.4, error: 0.4 },
        count: { projected_ks: 6.8, error: 0.8 },
        bayes: { projected_ks: 6.9, error: 0.9 },
      }],
    }
    const fetchMock = vi.fn((url) => (
      url.includes('/dates')
        ? apiResponse({ dates: [{ date: '2026-08-01', grading_status: 'graded' }] })
        : apiResponse(comparison)
    ))
    vi.stubGlobal('fetch', fetchMock)

    render(<PitcherKsComparisonPage />)

    await waitFor(() => expect(screen.getAllByText('Simulation').length).toBeGreaterThan(0))
    expect(screen.getByText('Error +0.40')).toBeInTheDocument()
    expect(screen.getByText('Best final MAE')).toBeInTheDocument()
    expect(screen.getByText('All eligible results are final.', { exact: false })).toBeInTheDocument()
  })
})
