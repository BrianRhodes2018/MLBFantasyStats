import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import TimePeriodSelector from './TimePeriodSelector'

describe('TimePeriodSelector', () => {
  it('renders custom pitcher windows when provided', () => {
    const onPeriodChange = vi.fn()
    const periods = [
      { value: 'season', label: 'Season' },
      { value: 30, label: 'Last 30 Days' },
      { value: 45, label: 'Last 45 Days' },
      { value: 60, label: 'Last 60 Days' },
    ]

    render(
      <TimePeriodSelector
        activePeriod="season"
        onPeriodChange={onPeriodChange}
        loading={false}
        periods={periods}
      />
    )

    expect(screen.queryByText('Last 5 Days')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Last 45 Days'))
    expect(onPeriodChange).toHaveBeenCalledWith(45)
  })
})
