/**
 * TimePeriodSelector.jsx - Rolling Time Period Toggle
 * ====================================================
 *
 * A row of buttons that lets the user switch between viewing full-season
 * stats and rolling time-window stats (Last 5, 10, 15, or 30 days).
 *
 * Key React concepts demonstrated:
 * - Controlled component: the active period is stored in the parent (App.jsx)
 *   and passed down as the `activePeriod` prop. This component doesn't manage
 *   its own state — it just renders buttons and calls `onPeriodChange` when clicked.
 * - Callback props: `onPeriodChange(period)` notifies the parent of user selections.
 * - Conditional CSS classes: the active button gets a `.active` class for styling.
 *
 * Props:
 * - activePeriod: The currently selected period ('season', 5, 10, 15, or 30)
 * - onPeriodChange: Callback function called with the new period when a button is clicked
 * - loading: Boolean — if true, buttons are disabled while data is being fetched
 */

function TimePeriodSelector({ activePeriod, onPeriodChange, loading }) {
  /**
   * Define the available time periods.
   *
   * Each period has:
   * - value: The identifier passed to onPeriodChange and used as the API query param.
   *          'season' means full-season stats (no rolling window).
   *          Numeric values (5, 10, 15, 30) represent days to look back.
   * - label: The text displayed on the button.
   *
   * These values map directly to the `days` query parameter in the
   * /players/rolling-stats and /pitchers/rolling-stats API endpoints.
   * When 'season' is selected, we fetch from /players/ and /pitchers/ instead.
   */
  const periods = [
    { value: 'season', label: 'Season' },
    { value: 5, label: 'Last 5 Days' },
    { value: 10, label: 'Last 10 Days' },
    { value: 15, label: 'Last 15 Days' },
    { value: 30, label: 'Last 30 Days' },
  ]

  return (
    <div className="time-period-selector">
      <span className="time-period-label">View Stats:</span>
      <div className="time-period-buttons">
        {periods.map((period) => (
          <button
            key={period.value}
            // Apply 'active' class when this button's period matches the selected one.
            // Template literal: `base-class ${conditional && 'extra-class'}`
            // The filter(Boolean) removes false/null values, .join(' ') creates the class string.
            className={[
              'time-period-btn',
              activePeriod === period.value && 'active',
            ].filter(Boolean).join(' ')}
            onClick={() => onPeriodChange(period.value)}
            // Disable buttons while rolling stats are loading to prevent
            // rapid-fire API calls from clicking multiple buttons quickly.
            disabled={loading}
          >
            {period.label}
          </button>
        ))}
      </div>
      {/* Show a subtle loading indicator while fetching rolling data */}
      {loading && <span className="time-period-loading">Loading...</span>}
    </div>
  )
}

export default TimePeriodSelector
