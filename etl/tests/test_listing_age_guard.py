import unittest

from scripts.listing_age_guard import is_listing_age_allowed


class ListingAgeGuardTest(unittest.TestCase):
    def test_excludes_before_30_calendar_days(self):
        self.assertFalse(is_listing_age_allowed("20260806", "20260904"))

    def test_allows_from_30_calendar_days(self):
        self.assertTrue(is_listing_age_allowed("20260805", "20260904"))

    def test_excludes_when_listing_date_is_unknown(self):
        self.assertFalse(is_listing_age_allowed(None, "20260904"))


if __name__ == "__main__":
    unittest.main()
