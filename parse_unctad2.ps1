$c = Get-Content 'D:\Project\unctad_ldc.html' -Raw -Encoding UTF8

# Search for key phrases that would appear near the claimed quote
$phrases = @(
    'Concessions associated with LDC',
    'benefits in the areas of',
    'Development financing, notably',
    'grants and loans from donors',
    'Multilateral trading system',
    'preferential market access and special treatments',
    'toward trade mainstreaming'
)

foreach ($p in $phrases) {
    $j = $c.IndexOf($p)
    if ($j -ge 0) {
        Write-Output "FOUND: '$p' at index $j"
    } else {
        Write-Output "NOT FOUND: '$p'"
    }
}

Write-Output ""
Write-Output "=== Searching for nearby content ==="
# Also search for key single words
$words = @('preferential', 'concession', 'grant', 'loan', 'IDA', 'WTO', 'market access')
foreach ($w in $words) {
    $count = ([regex]::Matches($c, [regex]::Escape($w))).Count
    if ($count -gt 0) {
        Write-Output "Word '$w' appears $count times"
    }
}
