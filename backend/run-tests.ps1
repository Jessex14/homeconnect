param(
    [switch]$UsePostgres
)

$ErrorActionPreference = 'Stop'
$env:DJANGO_DEBUG = 'true'
$env:DJANGO_SECRET_KEY = 'foundation-test-key-only'

if ($UsePostgres) {
    if (-not $env:DATABASE_URL) {
        throw 'Set DATABASE_URL to a disposable PostgreSQL database before using -UsePostgres.'
    }
    Remove-Item Env:DJANGO_TEST_SQLITE -ErrorAction SilentlyContinue
} else {
    $env:DJANGO_TEST_SQLITE = '1'
}

python manage.py test --verbosity 2
exit $LASTEXITCODE
