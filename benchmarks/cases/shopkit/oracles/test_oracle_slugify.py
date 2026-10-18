from shopkit.utils import slugify


def test_slugify_keeps_digits():
    assert slugify("Product 2 Pro") == "product-2-pro"
    assert slugify("USB-C Hub 4K") == "usb-c-hub-4k"


def test_slugify_still_strips_punctuation():
    assert slugify("Hello, World!") == "hello-world"
