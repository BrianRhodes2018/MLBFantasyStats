import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from xwoba import parse_savant_pitcher_expected_stats


def test_parse_savant_pitcher_expected_stats_extracts_xera_rows():
    html = """
    <script>
      var data = [{"entity_id":"645261","entity_name":"Pitcher, Test","pa":"123","xera":"3.79","era":"4.66","est_woba":0.309,"woba":0.332}];
    </script>
    """

    rows = parse_savant_pitcher_expected_stats(html)

    assert rows == [
        {
            "player_mlb_id": 645261,
            "pa": 123,
            "xera": 3.79,
            "era": 4.66,
            "xwoba": 0.309,
            "woba": 0.332,
            "xba": None,
            "xslg": None,
            "barrels_per_pa": None,
            "hard_hit_percent": None,
            "exit_velocity_avg": None,
        }
    ]
