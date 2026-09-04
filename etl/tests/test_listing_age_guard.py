import unittest

from scripts.listing_age_guard import is_listing_age_allowed, partition_by_listing_age


class ListingAgeGuardTest(unittest.TestCase):
    def test_excludes_before_30_calendar_days(self):
        self.assertFalse(is_listing_age_allowed("20260806", "20260904"))

    def test_allows_from_30_calendar_days(self):
        self.assertTrue(is_listing_age_allowed("20260805", "20260904"))

    def test_excludes_when_listing_date_is_unknown(self):
        self.assertFalse(is_listing_age_allowed(None, "20260904"))


if __name__ == "__main__":
    unittest.main()


class PartitionByListingAgeTest(unittest.TestCase):
    def test_splits_allowed_and_excluded_in_one_pass(self):
        first = {"005930": "20210802", "999999": "20260901"}
        allowed, excluded = partition_by_listing_age(
            ["005930", "999999"], first, "20260904"
        )
        self.assertEqual(allowed, ["005930"])
        self.assertEqual(excluded, ["999999"])

    def test_unknown_ticker_goes_to_excluded(self):
        allowed, excluded = partition_by_listing_age(["000000"], {}, "20260904")
        self.assertEqual(allowed, [])
        self.assertEqual(excluded, ["000000"])
