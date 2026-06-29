$c = Get-Content 'D:\Project\unctad_ldc.html' -Raw -Encoding UTF8
$i = $c.IndexOf('Concessions associated')
Write-Output "Index: $i"
if ($i -ge 0) {
    $s = [Math]::Max(0, $i-200)
    $len = [Math]::Min(3000, $c.Length - $s)
    $chunk = $c.Substring($s, $len)
    $clean = [regex]::Replace($chunk, '<[^>]+>', ' ')
    $clean = [regex]::Replace($clean, '\s+', ' ')
    Write-Output $clean.Substring(0, [Math]::Min(2000, $clean.Length))
} else {
    Write-Output 'Concessions phrase not found'
    $terms = @('development financing', 'multilateral trading', 'technical assistance', 'Enhanced Integrated Framework')
    foreach($t in $terms) {
        $j = $c.IndexOf($t)
        if ($j -ge 0) {
            Write-Output "Found '$t' at index $j"
            $ss = [Math]::Max(0, $j-100)
            $ll = [Math]::Min(500, $c.Length - $ss)
            $cc = $c.Substring($ss, $ll)
            $cc2 = [regex]::Replace($cc, '<[^>]+>', ' ')
            $cc2 = [regex]::Replace($cc2, '\s+', ' ')
            Write-Output $cc2.Substring(0, [Math]::Min(400, $cc2.Length))
            Write-Output ""
        }
    }
}
