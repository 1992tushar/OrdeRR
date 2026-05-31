"""
test_validate_name.py
---------------------
Unit tests for validate_restaurant_name() in order_service.py
"""

import pytest
from app.services.order_service import validate_restaurant_name


class TestValidRestaurantNames:
    def test_normal_name(self):
        assert validate_restaurant_name("Hotel Sai Krupa") is None

    def test_single_word(self):
        assert validate_restaurant_name("Fluffy") is None

    def test_hindi_name(self):
        assert validate_restaurant_name("Shree Hotel") is None

    def test_number_in_name(self):
        assert validate_restaurant_name("Hotel 786") is None

    def test_min_length_3(self):
        assert validate_restaurant_name("ABC") is None


class TestInvalidRestaurantNames:
    def test_too_short_one_char(self):
        assert validate_restaurant_name("A") is not None

    def test_too_short_two_chars(self):
        assert validate_restaurant_name("AB") is not None

    def test_too_long(self):
        assert validate_restaurant_name("A" * 61) is not None

    def test_only_numbers(self):
        assert validate_restaurant_name("9876543210") is not None

    def test_greeting_hi(self):
        assert validate_restaurant_name("hi") is not None

    def test_greeting_hello(self):
        assert validate_restaurant_name("hello") is not None

    def test_greeting_ok(self):
        assert validate_restaurant_name("ok") is not None

    def test_greeting_yes(self):
        assert validate_restaurant_name("yes") is not None

    def test_greeting_haan(self):
        assert validate_restaurant_name("haan") is not None

    def test_filler_send_menu(self):
        assert validate_restaurant_name("send menu") is not None

    def test_filler_place_order(self):
        assert validate_restaurant_name("place order") is not None

    def test_only_special_chars(self):
        assert validate_restaurant_name("!!!???") is not None

    def test_repeated_single_letter(self):
        assert validate_restaurant_name("aaaa") is not None
