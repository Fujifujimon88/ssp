"""Lock screen 5軸ターゲティング ユニットテスト"""
import pytest
from mdm.creative.selector import _matches_targeting


class TestMatchesTargeting:
    """_matches_targeting() の純粋関数テスト（DB不要）"""

    def test_empty_targeting_always_passes(self):
        assert _matches_targeting({}, platform="android", age_group="20s", region="tokyo", hour=12, screen_on_count=1)

    # time_slots
    def test_time_slots_match(self):
        t = {"time_slots": [7, 8, 9]}
        assert _matches_targeting(t, platform=None, age_group=None, region=None, hour=8, screen_on_count=None)

    def test_time_slots_no_match(self):
        t = {"time_slots": [7, 8, 9]}
        assert not _matches_targeting(t, platform=None, age_group=None, region=None, hour=12, screen_on_count=None)

    def test_time_slots_unknown_hour_passes(self):
        # hour=-1 は未知 → スキップ
        t = {"time_slots": [7, 8, 9]}
        assert _matches_targeting(t, platform=None, age_group=None, region=None, hour=-1, screen_on_count=None)

    # platform
    def test_platform_match(self):
        t = {"platform": "android"}
        assert _matches_targeting(t, platform="android", age_group=None, region=None, hour=-1, screen_on_count=None)

    def test_platform_no_match(self):
        t = {"platform": "android"}
        assert not _matches_targeting(t, platform="ios", age_group=None, region=None, hour=-1, screen_on_count=None)

    def test_platform_none_device_passes(self):
        # デバイスplatformが不明な場合はスキップ
        t = {"platform": "android"}
        assert _matches_targeting(t, platform=None, age_group=None, region=None, hour=-1, screen_on_count=None)

    # age_groups
    def test_age_group_match(self):
        t = {"age_groups": ["20s", "30s"]}
        assert _matches_targeting(t, platform=None, age_group="20s", region=None, hour=-1, screen_on_count=None)

    def test_age_group_no_match(self):
        t = {"age_groups": ["20s", "30s"]}
        assert not _matches_targeting(t, platform=None, age_group="40s", region=None, hour=-1, screen_on_count=None)

    # regions
    def test_region_match(self):
        t = {"regions": ["tokyo", "osaka"]}
        assert _matches_targeting(t, platform=None, age_group=None, region="tokyo", hour=-1, screen_on_count=None)

    def test_region_no_match(self):
        t = {"regions": ["tokyo"]}
        assert not _matches_targeting(t, platform=None, age_group=None, region="fukuoka", hour=-1, screen_on_count=None)

    # screen_on_count_max
    def test_screen_on_count_under_max(self):
        t = {"screen_on_count_max": 3}
        assert _matches_targeting(t, platform=None, age_group=None, region=None, hour=-1, screen_on_count=2)

    def test_screen_on_count_at_max(self):
        t = {"screen_on_count_max": 3}
        assert _matches_targeting(t, platform=None, age_group=None, region=None, hour=-1, screen_on_count=3)

    def test_screen_on_count_over_max(self):
        t = {"screen_on_count_max": 3}
        assert not _matches_targeting(t, platform=None, age_group=None, region=None, hour=-1, screen_on_count=4)

    def test_screen_on_count_none_passes(self):
        # screen_on_countが不明な場合はスキップ
        t = {"screen_on_count_max": 3}
        assert _matches_targeting(t, platform=None, age_group=None, region=None, hour=-1, screen_on_count=None)

    # AND logic
    def test_all_axes_match(self):
        t = {"time_slots": [8], "platform": "android", "age_groups": ["20s"], "regions": ["tokyo"], "screen_on_count_max": 3}
        assert _matches_targeting(t, platform="android", age_group="20s", region="tokyo", hour=8, screen_on_count=1)

    def test_one_axis_fails(self):
        t = {"time_slots": [8], "platform": "android", "age_groups": ["20s"]}
        # hourが合わない
        assert not _matches_targeting(t, platform="android", age_group="20s", region=None, hour=12, screen_on_count=None)
