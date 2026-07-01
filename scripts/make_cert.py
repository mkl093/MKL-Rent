"""Генерация самоподписанного TLS-сертификата для локального HTTPS (ТЗ §24, §41.2).

Камера для сканирования доступна только в защищённом контексте (HTTPS или localhost).
Для доступа со смартфона по локальной сети нужен HTTPS — этот скрипт создаёт
самоподписанный сертификат с вашими локальными IP в SAN.

Пример:
    python -m scripts.make_cert                 # авто-определение локальных IP
    python -m scripts.make_cert 192.168.1.5     # явно указать IP/хост
Файлы создаются в certs/ (cert.pem, key.pem). Папка certs/ в Git не коммитится.
"""

from __future__ import annotations

import datetime
import ipaddress
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CERTS_DIR = Path(__file__).parent.parent / "certs"


def _local_ipv4() -> list[str]:
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    # Надёжный способ узнать «исходящий» IP без реального соединения.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def main() -> int:
    extra_hosts = sys.argv[1:]
    CERTS_DIR.mkdir(parents=True, exist_ok=True)

    dns_names = ["localhost"]
    ip_addresses = ["127.0.0.1", "::1", *_local_ipv4()]
    for host in extra_hosts:
        try:
            ipaddress.ip_address(host)
            ip_addresses.append(host)
        except ValueError:
            dns_names.append(host)

    san: list[x509.GeneralName] = [x509.DNSName(d) for d in dict.fromkeys(dns_names)]
    san += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in dict.fromkeys(ip_addresses)]

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "rental-inventory dev")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    (CERTS_DIR / "key.pem").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    (CERTS_DIR / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print("Сертификат создан в", CERTS_DIR)
    print("  DNS:", ", ".join(dict.fromkeys(dns_names)))
    print("  IP :", ", ".join(dict.fromkeys(ip_addresses)))
    print("Запуск HTTPS:")
    print(
        "  uvicorn app.main:app --host 0.0.0.0 --port 8000 "
        "--ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
