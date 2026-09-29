from app.proxy import parse_proxy, parse_proxy_list


def test_proxy_formats():
    cases = {
        "1.2.3.4:8080": "http://1.2.3.4:8080",
        "u:p@1.2.3.4:8080": "http://u:p@1.2.3.4:8080",
        "1.2.3.4:8080:u:p": "http://u:p@1.2.3.4:8080",
        "u:p:1.2.3.4:8080": "http://u:p@1.2.3.4:8080",
        "1.2.3.4:8080@u:p": "http://u:p@1.2.3.4:8080",
        "socks5://u:p@proxy.example:1080": "socks5://u:p@proxy.example:1080",
    }
    for raw, expected in cases.items():
        assert parse_proxy(raw).url == expected


def test_proxy_list_counts_invalid_lines():
    parsed, skipped = parse_proxy_list("1.1.1.1:80\n\nnope\n# comment\n")
    assert len(parsed) == 1
    assert skipped == 1

