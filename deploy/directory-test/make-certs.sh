#!/bin/sh
# Test CA and LDAPS certificate for directory-ldap. The image's bundled certificate
# has expired, so the test directory uses these. Test-only keys, kept out of git (certs/).
set -eu
cd "$(dirname "$0")"
mkdir -p certs
cd certs
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 365 -subj "/CN=fabric test CA" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -keyout ldap.key -out ldap.csr -subj "/CN=directory-ldap" 2>/dev/null
printf 'subjectAltName=DNS:directory-ldap\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n' > ldap.ext
openssl x509 -req -in ldap.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out ldap.crt -days 365 -extfile ldap.ext 2>/dev/null
rm -f ldap.csr ldap.ext ca.srl
chmod 644 ldap.key   # read by the container's copy step (test key only)
echo "wrote $(pwd)/ca.crt (paste it into Admin › Directory), ldap.crt, ldap.key"
