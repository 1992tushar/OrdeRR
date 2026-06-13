ones = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
]

tens = [
    "", "", "Twenty", "Thirty", "Forty", "Fifty",
    "Sixty", "Seventy", "Eighty", "Ninety",
]


def _two_digits(n: int) -> str:
    if n == 0:
        return ""
    if n < 20:
        return ones[n]
    t = tens[n // 10]
    o = ones[n % 10]
    return f"{t} {o}".strip()


def _three_digits(n: int) -> str:
    if n == 0:
        return ""
    hundreds = n // 100
    remainder = n % 100
    parts = []
    if hundreds:
        parts.append(f"{ones[hundreds]} Hundred")
    if remainder:
        if hundreds:
            parts.append("and")
        parts.append(_two_digits(remainder))
    return " ".join(parts)


def _rupees_words(n: int) -> str:
    if n == 0:
        return "Zero"

    crore = n // 10_000_000
    n %= 10_000_000
    lakh = n // 100_000
    n %= 100_000
    thousand = n // 1_000
    n %= 1_000
    remainder = n

    parts = []
    if crore:
        parts.append(f"{_two_digits(crore)} Crore")
    if lakh:
        parts.append(f"{_two_digits(lakh)} Lakh")
    if thousand:
        parts.append(f"{_two_digits(thousand)} Thousand")
    if remainder:
        parts.append(_three_digits(remainder))

    return " ".join(parts)


def amount_in_words(amount: float) -> str:
    amount = round(amount, 2)
    rupees = int(amount)
    paise = round((amount - rupees) * 100)

    rupee_words = _rupees_words(rupees)
    result = f"Rupees {rupee_words}"

    if paise:
        paise_words = _two_digits(paise)
        result += f" and {paise_words} Paise"

    result += " Only"
    return result


if __name__ == "__main__":
    tests = [
        (0,           "Rupees Zero Only"),
        (1,           "Rupees One Only"),
        (10,          "Rupees Ten Only"),
        (100,         "Rupees One Hundred Only"),
        (765,         "Rupees Seven Hundred and Sixty Five Only"),
        (1000,        "Rupees One Thousand Only"),
        (10000,       "Rupees Ten Thousand Only"),
        (100000,      "Rupees One Lakh Only"),
        (1500000,     "Rupees Fifteen Lakh Only"),
        (10000000,    "Rupees One Crore Only"),
        (765.50,      "Rupees Seven Hundred and Sixty Five and Fifty Paise Only"),
        (99999999.99, "Rupees Nine Crore Ninety Nine Lakh Ninety Nine Thousand Nine Hundred and Ninety Nine and Ninety Nine Paise Only"),
    ]

    passed = 0
    for amount, expected in tests:
        result = amount_in_words(amount)
        status = "PASS" if result == expected else "FAIL"
        if status == "PASS":
            passed += 1
        else:
            print(f"{status}: amount_in_words({amount})")
            print(f"  Expected: {expected}")
            print(f"  Got:      {result}")

    print(f"\n{passed}/{len(tests)} tests passed.")
    if passed == len(tests):
        print("All tests passed!")