<#
=====================================================================
  RADAR DE LICITAÇÕES PARA MEI / ME / EPP  —  protótipo
=====================================================================
  O que ele faz:
    1. Busca no PNCP (Portal Nacional de Contratações Públicas) as
       licitações ABERTAS da região configurada abaixo.
    2. Olha os itens de cada uma e descobre o que é bom pra pequenos
       (itens exclusivos ME/EPP, cota reservada, valor baixo, dispensa).
    3. Escreve um resumo em linguagem MUITO simples para cada uma.
       (Se tiver chave do Gemini, a IA lê o edital e escreve o resumo.)
    4. Descobre EMPRESAS MEI / ME / EPP que já vendem para esses
       órgãos e que provavelmente se interessam pelas licitações abertas.
    5. Gera: relatório HTML, planilhas CSV (abrem no Excel) e
       mensagens prontas pra WhatsApp na pasta "saida".

  Como rodar: dê dois cliques em RODAR-RADAR.bat
  Modo rápido (só São José dos Pinhais, pra testar): RODAR-RAPIDO.bat
=====================================================================
#>
param(
    [switch]$Rapido,       # só SJP e limites menores (teste)
    [switch]$SemEmpresas,  # pula a busca de empresas
    [switch]$SemIA         # não usa o Gemini mesmo se tiver chave
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

# =====================================================================
#  CONFIGURAÇÃO  (pode mexer aqui)
# =====================================================================

# Cidades que o radar vai olhar (código IBGE = nome)
$Municipios = [ordered]@{
    '4125506' = 'São José dos Pinhais'
    '4106902' = 'Curitiba'
    '4119152' = 'Pinhais'
    '4101804' = 'Araucária'
    '4105805' = 'Colombo'
    '4107652' = 'Fazenda Rio Grande'
    '4104204' = 'Campo Largo'
    '4119509' = 'Piraquara'
    '4100400' = 'Almirante Tamandaré'
}

$DiasPraFrente            = 120   # pega licitações com prazo até daqui X dias
$MaxComprasDetalhar       = 100   # quantas licitações terão os itens analisados
$DiasHistoricoContratos   = 180   # olha contratos dos últimos X dias pra achar empresas
$MaxOrgaosParaEmpresas    = 20    # quantos órgãos analisar na busca de empresas
$MaxPaginasContratos      = 3     # páginas (de 50) de contratos por órgão
$MaxEmpresasConsultar     = 80    # quantas empresas consultar na Receita (BrasilAPI)
$MaxResumosIA             = 15    # quantas licitações recebem resumo da IA
$UsarPdfDoEdital          = $true # a IA lê o PDF do edital (mais completo, mais lento)
$ModeloGemini             = 'gemini-2.5-flash'
$PausaEntreChamadasMs     = 700   # o PNCP bloqueia se chamar rápido demais
$LimiteValorPequeno       = 80000 # LC 123: até R$ 80 mil é exclusivo pra ME/EPP

# Modalidades ignoradas: 1/13 leilão (governo vendendo), 9 inexigibilidade
# (sem disputa), 10 manifestação de interesse, 11 pré-qualificação
$ModalidadesIgnoradas = @(1, 9, 10, 11, 13)

if ($Rapido) {
    $Municipios = [ordered]@{ '4125506' = 'São José dos Pinhais' }
    $MaxComprasDetalhar = 30; $MaxOrgaosParaEmpresas = 3; $MaxEmpresasConsultar = 25; $MaxResumosIA = 5
}

# =====================================================================
#  PREPARAÇÃO
# =====================================================================
$Pasta      = Split-Path -Parent $MyInvocation.MyCommand.Path
$PastaSaida = Join-Path $Pasta 'saida'
$PastaCache = Join-Path $Pasta 'cache\cnpj'
New-Item -ItemType Directory -Force -Path $PastaSaida, $PastaCache | Out-Null

$Agora   = Get-Date
$Hoje    = $Agora.ToString('yyyyMMdd')
$Carimbo = $Agora.ToString('yyyy-MM-dd_HHmm')
$BR      = [Globalization.CultureInfo]::GetCultureInfo('pt-BR')
$Utf8Bom = New-Object System.Text.UTF8Encoding $true

# Chave do Gemini: variável de ambiente GEMINI_API_KEY ou arquivo chave-gemini.txt
$GeminiKey = $env:GEMINI_API_KEY
$arqChave = Join-Path $Pasta 'chave-gemini.txt'
if (-not $GeminiKey -and (Test-Path $arqChave)) { $GeminiKey = (Get-Content $arqChave -Raw).Trim() }
if ($SemIA) { $GeminiKey = $null }

function Escrever($msg, $cor = 'Gray') { Write-Host $msg -ForegroundColor $cor }

# =====================================================================
#  FUNÇÕES DE APOIO
# =====================================================================
function Invoke-Api {
    param([string]$Url, [int]$Pausa = $PausaEntreChamadasMs)
    for ($tentativa = 1; $tentativa -le 5; $tentativa++) {
        Start-Sleep -Milliseconds $Pausa
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 90
            if ($r.StatusCode -eq 204) { return $null }
            $txt = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
            if ([string]::IsNullOrWhiteSpace($txt)) { return $null }
            if ($txt.TrimStart().StartsWith('<')) { throw 'Limite de requisicoes (pagina HTML)' }
            return ($txt | ConvertFrom-Json)
        } catch {
            $status = $null
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            if ($status -eq 404 -or $status -eq 400) { return $null }
            $msg = "$_"
            $limite = ($status -eq 429) -or ($msg -match 'Limite|limite|Requisi')
            if ($tentativa -eq 5) { Escrever "   ! falhou: $Url ($msg)" 'DarkYellow'; return $null }
            $espera = if ($limite) { 20 * $tentativa } else { 3 * $tentativa }
            Escrever "   ... servidor pediu pra esperar, aguardando $espera s" 'DarkGray'
            Start-Sleep -Seconds $espera
        }
    }
}

function Get-Data($v) {
    if (-not $v) { return $null }
    if ($v -is [datetime]) { return $v }
    try { return [datetime]::Parse([string]$v, [Globalization.CultureInfo]::InvariantCulture) } catch { return $null }
}

function Format-Reais($v) {
    if ($null -eq $v -or [double]$v -le 0) { return 'não informado' }
    return 'R$ ' + ([double]$v).ToString('N2', $BR)
}

function Remove-Acentos([string]$s) {
    if (-not $s) { return '' }
    $n = $s.Normalize([Text.NormalizationForm]::FormD)
    $sb = New-Object Text.StringBuilder
    foreach ($ch in $n.ToCharArray()) {
        if ([Globalization.CharUnicodeInfo]::GetUnicodeCategory($ch) -ne [Globalization.UnicodeCategory]::NonSpacingMark) { [void]$sb.Append($ch) }
    }
    return $sb.ToString()
}

$Stop = @{}
('registro precos preco aquisicao aquisicoes contratacao contratacoes empresa empresas especializada especializadas ' +
 'servico servicos prestacao fornecimento municipal municipais municipio secretaria atender atendimento demanda demandas ' +
 'necessidade necessidades necessarios necessarias necessario conforme para pela pelo pelas pelos com dos das sao jose ' +
 'pinhais curitiba eventual eventuais futura futuras objeto destinados destinadas destinado diversas diversos unidade unidades ' +
 'termo referencia edital anexo anexos contrato especificacoes quantidades quantidade estimadas estimada periodo meses forma ' +
 'parcelada material materiais comum comuns natureza continuidade qualidade publica publico publicas publicos estado parana ' +
 'prefeitura departamento setor rede geral tipo item itens lote lotes valor total global menor segundo acordo sobre entre ' +
 'durante mediante atraves visando objetivando atendendo utilizacao execucao realizacao incluindo inclusive demais outros ' +
 'outras sendo serao sera mais suas seus como esta este essa esse onde quando visto vista atividades atividade programa ' +
 'gestao fundo ltda eireli comercio industria meses doze anos administracao contratada contratante obriga presente ' +
 'cnpj lei federal estadual nacional saude educacao') -split ' ' | ForEach-Object { $Stop[$_] = $true }

function Get-Tokens([string]$texto) {
    $set = @{}
    $t = (Remove-Acentos $texto).ToLower()
    foreach ($w in [regex]::Split($t, '[^a-z]+')) {
        if ($w.Length -lt 4 -or $Stop.ContainsKey($w)) { continue }
        if ($w.Length -gt 4 -and $w.EndsWith('s')) { $w = $w.Substring(0, $w.Length - 1) }
        if ($Stop.ContainsKey($w)) { continue }
        $set[$w] = $true
    }
    return $set
}

function Get-Similaridade($a, $b) {
    $pontos = 0.0; $comuns = @()
    foreach ($k in $a.Keys) { if ($b.ContainsKey($k)) { $pontos += $(if ($k.Length -ge 8) { 1.5 } else { 1 }); $comuns += $k } }
    return @{ Pontos = $pontos; Comuns = $comuns }
}

function Get-ObjetoSimples([string]$obj) {
    if (-not $obj) { return '(sem descrição)' }
    $s = ($obj -replace '\s+', ' ').Trim()
    $letras = ($s -replace '[^A-Za-zÀ-ú]', '')
    $maius  = ($s -creplace '[^A-ZÀ-Þ]', '')
    if ($letras.Length -gt 0 -and ($maius.Length / $letras.Length) -gt 0.6) { $s = $s.ToLower() }
    $padroes = @(
        '^(o\s+|a\s+)?registros?\s+de\s+pre[cç]os?\s*(-|–|:|,)?\s*(para|visando|objetivando|destinado\s+[aà])?\s*',
        '^(a\s+)?(futura\s+e\s+eventual\s+|eventual\s+e\s+futura\s+|eventual\s+)?(aquisi[cç][aã]o|contrata[cç][aã]o|compra)\s+(de\s+|da\s+|do\s+)?',
        '^(empresa\(?s?\)?|pessoa\s+jur[ií]dica)\s+(especializada\(?s?\)?\s+)?(para|em|na|no|visando)?\s*(a\s+|o\s+)?',
        '^(fornecimento|presta[cç][aã]o)\s+(de\s+|dos\s+|das\s+|do\s+|da\s+)?'
    )
    foreach ($p in $padroes) { $s = [regex]::Replace($s, $p, '', 'IgnoreCase') }
    if ($s.Length -gt 220) { $corte = $s.LastIndexOf(' ', 220); if ($corte -lt 100) { $corte = 220 }; $s = $s.Substring(0, $corte).TrimEnd(',', ';', ' ') + '...' }
    if ($s.Length -gt 0) { $s = $s.Substring(0, 1).ToUpper() + $s.Substring(1) }
    return $s
}

function Get-ModalidadeSimples($id, $nome) {
    switch ([int]$id) {
        8  { return 'Compra direta (dispensa): é o tipo mais simples, geralmente de valor menor e com menos papelada.' }
        6  { return 'Pregão pela internet: as empresas dão lances e ganha quem cobrar menos.' }
        7  { return 'Pregão presencial: disputa de preço feita pessoalmente, no órgão.' }
        4  { return 'Concorrência pela internet: disputa maior, costuma pedir mais documentos.' }
        5  { return 'Concorrência presencial: disputa maior, feita pessoalmente.' }
        12 { return 'Credenciamento: não tem disputa. Toda empresa que cumprir as regras pode se cadastrar e ser chamada.' }
        default { return "$nome (modalidade especial, leia o edital com calma)." }
    }
}

function Format-Telefone($t) {
    $d = ($t -replace '\D', '')
    if ($d.Length -eq 10) { return '({0}) {1}-{2}' -f $d.Substring(0,2), $d.Substring(2,4), $d.Substring(6) }
    if ($d.Length -eq 11) { return '({0}) {1}-{2}' -f $d.Substring(0,2), $d.Substring(2,5), $d.Substring(7) }
    return $d
}

function Format-Cnpj($c) {
    $d = ($c -replace '\D', '')
    if ($d.Length -ne 14) { return $c }
    return '{0}.{1}.{2}/{3}-{4}' -f $d.Substring(0,2), $d.Substring(2,3), $d.Substring(5,3), $d.Substring(8,4), $d.Substring(12)
}

function HtmlEnc([string]$s) {
    if (-not $s) { return '' }
    return $s.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;').Replace('"', '&quot;')
}

function Get-NomeOrgao($op) {
    if ($op.Orgao -match '^MUNIC[IÍ]PIO DE' -and $op.Cidade) { return "Prefeitura de $($op.Cidade)" }
    $nome = $BR.TextInfo.ToTitleCase($op.Orgao.ToLower())
    return [regex]::Replace($nome, ' (De|Da|Do|Das|Dos|E) ', [Text.RegularExpressions.MatchEvaluator]{ param($m) ' ' + $m.Groups[1].Value.ToLower() + ' ' })
}

function Lower-Primeira([string]$s) {
    if ($s.Length -ge 2 -and [char]::IsLower($s[1])) { return $s.Substring(0, 1).ToLower() + $s.Substring(1) }
    return $s
}

# =====================================================================
#  INTELIGÊNCIA ARTIFICIAL (Gemini) — opcional
# =====================================================================
function Invoke-Gemini([string]$prompt, [byte[]]$pdf) {
    $promptJson = $prompt | ConvertTo-Json
    $partes = ''
    if ($pdf) { $partes = '{"inline_data":{"mime_type":"application/pdf","data":"' + [Convert]::ToBase64String($pdf) + '"}},' }
    $body = '{"contents":[{"parts":[' + $partes + '{"text":' + $promptJson + '}]}],"generationConfig":{"temperature":0.3}}'
    $url = "https://generativelanguage.googleapis.com/v1beta/models/$($ModeloGemini):generateContent"
    $r = Invoke-WebRequest -Uri $url -Method Post -Headers @{ 'x-goog-api-key' = $GeminiKey } `
        -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing -TimeoutSec 240
    $j = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
    return (($j.candidates[0].content.parts | ForEach-Object { $_.text }) -join '').Trim()
}

function Get-PdfEdital($op) {
    $arqs = Invoke-Api "https://pncp.gov.br/api/pncp/v1/orgaos/$($op.Cnpj)/compras/$($op.Ano)/$($op.Seq)/arquivos"
    if (-not $arqs) { return $null }
    $alvo = @($arqs | Where-Object { $_.tipoDocumentoNome -match 'Edital|Aviso|Termo de Refer' }) + @($arqs) | Select-Object -First 1
    if (-not $alvo.url) { return $null }
    try {
        Start-Sleep -Milliseconds $PausaEntreChamadasMs
        $r = Invoke-WebRequest -Uri $alvo.url -UseBasicParsing -TimeoutSec 120
        $bytes = $r.RawContentStream.ToArray()
        if ($bytes.Length -lt 5 -or $bytes.Length -gt 15MB) { return $null }
        if ([Text.Encoding]::ASCII.GetString($bytes, 0, 4) -ne '%PDF') { return $null }
        return ,$bytes
    } catch { return $null }
}

# =====================================================================
#  1) LICITAÇÕES ABERTAS
# =====================================================================
Escrever "`n=== RADAR DE LICITAÇÕES PARA MEI / ME / EPP ===" 'Cyan'
Escrever "Data: $($Agora.ToString('dd/MM/yyyy HH:mm'))   Cidades: $($Municipios.Count)   IA: $(if ($GeminiKey) {'ligada'} else {'desligada (sem chave)'})`n"
Escrever '[1/5] Buscando licitações abertas no PNCP...' 'Yellow'

$dataFinal = $Agora.AddDays($DiasPraFrente).ToString('yyyyMMdd')
$brutas = @{}
foreach ($cod in $Municipios.Keys) {
    $pag = 1; $qtd = 0
    do {
        $resp = Invoke-Api "https://pncp.gov.br/api/consulta/v1/contratacoes/proposta?dataFinal=$dataFinal&codigoMunicipioIbge=$cod&pagina=$pag&tamanhoPagina=50"
        if (-not $resp -or -not $resp.data) { break }
        foreach ($c in $resp.data) { $brutas[$c.numeroControlePNCP] = $c; $qtd++ }
        $totalPag = [int]$resp.totalPaginas
        $pag++
    } while ($pag -le $totalPag)
    Escrever ("   {0,-22} {1,4} abertas" -f $Municipios[$cod], $qtd)
}

$ops = New-Object System.Collections.ArrayList
foreach ($c in $brutas.Values) {
    if ($ModalidadesIgnoradas -contains [int]$c.modalidadeId) { continue }
    $fim = Get-Data $c.dataEncerramentoProposta
    if ($fim -and $fim -lt $Agora) { continue }
    $valor = 0.0; if ($c.valorTotalEstimado) { $valor = [double]$c.valorTotalEstimado }
    $op = [pscustomobject]@{
        Id = $c.numeroControlePNCP; Cnpj = $c.orgaoEntidade.cnpj; Ano = $c.anoCompra; Seq = $c.sequencialCompra
        Orgao = $c.orgaoEntidade.razaoSocial; Unidade = $c.unidadeOrgao.nomeUnidade; Cidade = $c.unidadeOrgao.municipioNome
        Objeto = $c.objetoCompra; ObjetoSimples = (Get-ObjetoSimples $c.objetoCompra)
        ModalidadeId = [int]$c.modalidadeId; Modalidade = $c.modalidadeNome; Srp = [bool]$c.srp
        Valor = $valor; Abertura = (Get-Data $c.dataAberturaProposta); Fim = $fim
        DiasRestantes = $(if ($fim) { [math]::Floor(($fim - $Agora).TotalDays) } else { $null })
        Link = "https://pncp.gov.br/app/editais/$($c.orgaoEntidade.cnpj)/$($c.anoCompra)/$($c.sequencialCompra)"
        LinkSistema = $c.linkSistemaOrigem
        Itens = @(); ItensExclusivos = @(); ItensPequenos = @(); TemCota = $false
        Pontos = 0; Nivel = ''; Motivos = @(); Texto = ''; TextoIA = ''; Tokens = $null; Empresas = @()
    }
    # pontuação inicial (antes de ver os itens)
    $p = 0
    if ($op.ModalidadeId -eq 8) { $p += 2 }
    if ($op.Valor -gt 0 -and $op.Valor -le $LimiteValorPequeno) { $p += 2 } elseif ($op.Valor -gt 0 -and $op.Valor -le 300000) { $p += 1 }
    if ($op.Cidade -match 'José dos Pinhais') { $p += 1 }
    $op.Pontos = $p
    [void]$ops.Add($op)
}
Escrever "   Total útil: $($ops.Count) licitações abertas`n" 'Green'
if ($ops.Count -eq 0) { Escrever 'Nenhuma licitação aberta encontrada. Fim.' 'Red'; exit }

# =====================================================================
#  2) ITENS: o que é bom pra pequeno?
# =====================================================================
Escrever "[2/5] Analisando os itens (até $MaxComprasDetalhar licitações)..." 'Yellow'
$detalhar = $ops | Sort-Object @{e={$_.Pontos};Descending=$true}, @{e={$_.Fim}} | Select-Object -First $MaxComprasDetalhar
$n = 0
foreach ($op in $detalhar) {
    $n++
    if ($n % 10 -eq 0) { Escrever "   $n de $(@($detalhar).Count)..." 'DarkGray' }
    $itens = @(); $pag = 1
    do {
        $lote = Invoke-Api "https://pncp.gov.br/api/pncp/v1/orgaos/$($op.Cnpj)/compras/$($op.Ano)/$($op.Seq)/itens?pagina=$pag&tamanhoPagina=100"
        if (-not $lote) { break }
        $lote = @($lote); $itens += $lote; $pag++
    } while ($lote.Count -eq 100 -and $pag -le 5)
    $op.Itens = $itens
    $op.ItensExclusivos = @($itens | Where-Object { $_.tipoBeneficio -eq 1 -or $_.tipoBeneficio -eq 3 -or ($_.tipoBeneficioNome -match 'exclusiva|Cota') })
    $op.TemCota = [bool](@($itens | Where-Object { $_.tipoBeneficio -eq 3 -or $_.tipoBeneficioNome -match 'Cota' }).Count)
    $op.ItensPequenos = @($itens | Where-Object { $_.valorTotal -and [double]$_.valorTotal -gt 0 -and [double]$_.valorTotal -le $LimiteValorPequeno })
}

foreach ($op in $ops) {
    $p = 0; $m = @()
    $nEx = @($op.ItensExclusivos).Count; $nIt = @($op.Itens).Count
    if ($nEx -gt 0) {
        $p += 3
        if ($nEx -eq $nIt) { $m += 'Só MEI, ME e EPP podem participar' } else { $m += "$nEx de $nIt itens são só pra MEI, ME e EPP" }
    }
    if ($op.ModalidadeId -eq 8) { $p += 2; $m += 'Compra direta (mais simples)' }
    if ($op.Valor -gt 0 -and $op.Valor -le $LimiteValorPequeno) { $p += 2; $m += 'Valor pequeno' }
    elseif ($op.Valor -gt 0 -and $op.Valor -le 300000) { $p += 1 }
    if (@($op.ItensPequenos).Count -gt 0 -and $nEx -eq 0) { $p += 1; $m += 'Tem itens de valor baixo' }
    if ($op.Valor -gt 1000000 -and $nEx -eq 0) { $p -= 2; $m += 'Valor alto: mais difícil pra pequeno' }
    if ($op.Cidade -match 'José dos Pinhais') { $p += 1 }
    $op.Pontos = $p; $op.Motivos = $m
    $op.Nivel = if ($p -ge 4) { 'otima' } elseif ($p -ge 2) { 'talvez' } else { 'dificil' }
    $textoItens = ($op.Itens | Select-Object -First 40 | ForEach-Object { $_.descricao }) -join ' '
    $op.Tokens = Get-Tokens ($op.Objeto + ' ' + $textoItens)
}
Escrever "   Pronto.`n" 'Green'

# =====================================================================
#  3) TEXTO SIMPLES (e IA, se tiver chave)
# =====================================================================
Escrever '[3/5] Escrevendo os resumos em linguagem simples...' 'Yellow'
foreach ($op in $ops) {
    $verbo = if ($op.Objeto -match '(?i)servi|contrata|loca[cç]|manuten') { 'contratar' } else { 'comprar' }
    $quem  = Get-NomeOrgao $op
    $L = New-Object System.Collections.ArrayList
    [void]$L.Add("O QUE QUEREM: $quem quer $verbo $(Lower-Primeira $op.ObjetoSimples)")
    [void]$L.Add("QUANTO VALE: $(Format-Reais $op.Valor) (valor total estimado)")
    if ($op.Fim) {
        $falta = if ($op.DiasRestantes -le 0) { 'termina HOJE' } elseif ($op.DiasRestantes -eq 1) { 'falta 1 dia' } else { "faltam $($op.DiasRestantes) dias" }
        [void]$L.Add("PRAZO: mandar proposta até $($op.Fim.ToString('dd/MM/yyyy')) às $($op.Fim.ToString('HH:mm')) ($falta)")
    }
    [void]$L.Add("COMO FUNCIONA: $(Get-ModalidadeSimples $op.ModalidadeId $op.Modalidade)")
    $nEx = @($op.ItensExclusivos).Count
    if ($nEx -gt 0 -and $nEx -eq @($op.Itens).Count) { [void]$L.Add('PEQUENAS EMPRESAS: esta é SÓ para MEI, ME e EPP. Empresa grande não entra.') }
    elseif ($nEx -gt 0) { [void]$L.Add("PEQUENAS EMPRESAS: $nEx item(ns) são SÓ para MEI, ME e EPP.") }
    else { [void]$L.Add('PEQUENAS EMPRESAS: aberta pra todos, mas o pequeno ainda tem vantagem no desempate.') }
    if ($op.Srp) { [void]$L.Add('ATENÇÃO: é "registro de preços". A prefeitura fecha o preço e vai pedindo aos poucos, por até 1 ano. Não é garantia de comprar tudo.') }
    [void]$L.Add("ONDE VER: $($op.Link)")
    $op.Texto = $L -join "`n"
}

if ($GeminiKey) {
    $paraIA = $ops | Sort-Object @{e={$_.Pontos};Descending=$true}, @{e={$_.Fim}} | Select-Object -First $MaxResumosIA
    $n = 0
    foreach ($op in $paraIA) {
        $n++; Escrever "   IA lendo $n de $(@($paraIA).Count): $($op.ObjetoSimples.Substring(0, [math]::Min(60, $op.ObjetoSimples.Length)))" 'DarkGray'
        $listaItens = ($op.Itens | Select-Object -First 25 | ForEach-Object { "- $($_.descricao) | qtd: $($_.quantidade) $($_.unidadeMedida) | valor: $(Format-Reais $_.valorTotal) | $($_.tipoBeneficioNome)" }) -join "`n"
        $prompt = @"
Você vai ajudar donos de MEI e de pequenas empresas que NUNCA participaram de licitação.
Escreva em português bem simples, com frases curtas, como se falasse com um vizinho.
Não use juridiquês. Se precisar usar um termo técnico, explique entre parênteses.
Use os dados abaixo e, se houver, o edital em anexo. NÃO invente nada: se não souber, escreva "não informado".
Responda EXATAMENTE neste formato (sem negrito, sem markdown):

O QUE QUEREM: (1 ou 2 frases)
QUEM PODE PARTICIPAR: (diga claramente se é só para MEI/ME/EPP)
DOCUMENTOS QUE PEDEM:
- (no máximo 6 itens; se não souber, escreva "ver no edital")
ATENÇÃO:
- (até 3 cuidados importantes: prazo de entrega, amostra, visita técnica, local de entrega, garantia...)
VALE A PENA PRA UM MEI? (Sim / Talvez / Difícil) + o motivo em 1 frase.

DADOS:
Órgão: $($op.Orgao) - $($op.Cidade)
Modalidade: $($op.Modalidade)
Objeto: $($op.Objeto)
Valor total estimado: $(Format-Reais $op.Valor)
Prazo para proposta: $(if ($op.Fim) { $op.Fim.ToString('dd/MM/yyyy HH:mm') } else { 'não informado' })
Registro de preços: $(if ($op.Srp) {'sim'} else {'não'})
Itens:
$listaItens
"@
        $pdf = $null
        if ($UsarPdfDoEdital) { $pdf = Get-PdfEdital $op }
        try { $op.TextoIA = Invoke-Gemini $prompt $pdf }
        catch {
            if ($pdf) { try { $op.TextoIA = Invoke-Gemini $prompt $null } catch { Escrever "   ! IA falhou: $_" 'DarkYellow' } }
            else { Escrever "   ! IA falhou: $_" 'DarkYellow' }
        }
    }
} else {
    Escrever '   (sem chave do Gemini: usando resumo automático simples)' 'DarkGray'
}
Escrever "   Pronto.`n" 'Green'

# =====================================================================
#  4) EMPRESAS MEI / ME / EPP que provavelmente se interessam
# =====================================================================
$empresas = @()
if (-not $SemEmpresas) {
    Escrever '[4/5] Procurando empresas MEI / ME / EPP que já vendem pra esses órgãos...' 'Yellow'
    $orgaos = $ops | Group-Object Cnpj | Sort-Object Count -Descending | Select-Object -First $MaxOrgaosParaEmpresas
    $ini = $Agora.AddDays(-$DiasHistoricoContratos).ToString('yyyyMMdd')
    $forn = @{}
    foreach ($g in $orgaos) {
        $nomeOrgao = $g.Group[0].Orgao
        $pag = 1; $qtd = 0
        do {
            $resp = Invoke-Api "https://pncp.gov.br/api/consulta/v1/contratos?dataInicial=$ini&dataFinal=$Hoje&cnpjOrgao=$($g.Name)&pagina=$pag&tamanhoPagina=50"
            if (-not $resp -or -not $resp.data) { break }
            foreach ($ct in $resp.data) {
                $ni = ($ct.niFornecedor -replace '\D', '')
                if ($ct.tipoPessoa -ne 'PJ' -or $ni.Length -ne 14) { continue }
                if (-not $forn.ContainsKey($ni)) { $forn[$ni] = [pscustomobject]@{ Cnpj = $ni; Nome = $ct.nomeRazaoSocialFornecedor; Contratos = 0; ValorTotal = 0.0; Objetos = New-Object System.Collections.ArrayList; Orgaos = @{} } }
                $f = $forn[$ni]; $f.Contratos++; if ($ct.valorGlobal) { $f.ValorTotal += [double]$ct.valorGlobal }
                if ($f.Objetos.Count -lt 12) { [void]$f.Objetos.Add($ct.objetoContrato) }
                $f.Orgaos[$nomeOrgao] = $true; $qtd++
            }
            $totalPag = [int]$resp.totalPaginas; $pag++
        } while ($pag -le $totalPag -and $pag -le $MaxPaginasContratos)
        Escrever ("   {0,-50} {1,4} contratos" -f $nomeOrgao.Substring(0, [math]::Min(50, $nomeOrgao.Length)), $qtd)
    }
    Escrever "   $($forn.Count) fornecedores encontrados. Cruzando com as licitações abertas..." 'DarkGray'

    $boas = $ops | Where-Object { $_.Nivel -ne 'dificil' }
    if (-not $boas) { $boas = $ops }
    foreach ($f in $forn.Values) {
        $tk = Get-Tokens ($f.Objetos -join ' ')
        $matches2 = @()
        foreach ($op in $boas) {
            $s = Get-Similaridade $tk $op.Tokens
            if ($s.Pontos -ge 2) { $matches2 += [pscustomobject]@{ Op = $op; Pontos = $s.Pontos; Comuns = $s.Comuns } }
        }
        $f | Add-Member -NotePropertyName Tokens -NotePropertyValue $tk -Force
        $f | Add-Member -NotePropertyName Matches -NotePropertyValue @($matches2 | Sort-Object Pontos -Descending) -Force
        $f | Add-Member -NotePropertyName Relevancia -NotePropertyValue ((@($matches2 | Measure-Object Pontos -Sum).Sum) + [math]::Min($f.Contratos, 5) * 0.2) -Force
    }
    # quem tem oportunidade parecida vem primeiro; quem tem valor gigante (empresa grande) vai pro fim
    $candidatos = $forn.Values | Where-Object { $_.ValorTotal -lt 5000000 } | Sort-Object @{e={$_.Matches.Count -gt 0};Descending=$true}, @{e={$_.Relevancia};Descending=$true} | Select-Object -First $MaxEmpresasConsultar

    Escrever "   Consultando porte e contato na Receita (BrasilAPI) de até $(@($candidatos).Count) empresas..." 'DarkGray'
    $n = 0
    foreach ($f in $candidatos) {
        $n++; if ($n % 10 -eq 0) { Escrever "   $n de $(@($candidatos).Count)..." 'DarkGray' }
        $cache = Join-Path $PastaCache "$($f.Cnpj).json"
        $rf = $null
        if (Test-Path $cache) { $rf = Get-Content $cache -Raw -Encoding UTF8 | ConvertFrom-Json }
        else {
            $rf = Invoke-Api "https://brasilapi.com.br/api/cnpj/v1/$($f.Cnpj)" -Pausa 1300
            if ($rf) { [IO.File]::WriteAllText($cache, ($rf | ConvertTo-Json -Depth 6), $Utf8Bom) }
        }
        if (-not $rf) { continue }
        if ($rf.descricao_situacao_cadastral -and $rf.descricao_situacao_cadastral -ne 'ATIVA') { continue }
        $porte = $null
        if ($rf.opcao_pelo_mei -eq $true) { $porte = 'MEI' }
        elseif ($rf.porte -match 'MICRO') { $porte = 'ME' }
        elseif ($rf.porte -match 'PEQUENO') { $porte = 'EPP' }
        if (-not $porte) { continue }   # só MEI, ME e EPP

        # recalcula com a atividade principal da empresa
        $tk2 = Get-Tokens (($f.Objetos -join ' ') + ' ' + $rf.cnae_fiscal_descricao)
        $mt = @()
        foreach ($op in $boas) { $s = Get-Similaridade $tk2 $op.Tokens; if ($s.Pontos -ge 2) { $mt += [pscustomobject]@{ Op = $op; Pontos = $s.Pontos; Comuns = $s.Comuns } } }
        $mt = @($mt | Sort-Object Pontos -Descending | Select-Object -First 5)
        $tel = @($rf.ddd_telefone_1, $rf.ddd_telefone_2 | Where-Object { $_ } | ForEach-Object { Format-Telefone $_ }) -join ' / '

        $emp = [pscustomobject]@{
            Empresa = $rf.razao_social; NomeFantasia = $rf.nome_fantasia; CNPJ = (Format-Cnpj $f.Cnpj); Porte = $porte
            Cidade = "$($rf.municipio)/$($rf.uf)"; Telefone = $tel; Email = ([string]$rf.email).ToLower()
            Atividade = $rf.cnae_fiscal_descricao
            JaVendeuPara = (($f.Orgaos.Keys | Select-Object -First 3) -join '; ')
            ContratosRecentes = $f.Contratos; ValorContratos = $f.ValorTotal
            ExemploVenda = (Get-ObjetoSimples $f.Objetos[0])
            QtdOportunidades = $mt.Count
            Oportunidades = (($mt | ForEach-Object { "$($_.Op.ObjetoSimples.Substring(0, [math]::Min(70, $_.Op.ObjetoSimples.Length))) (até $(if ($_.Op.Fim) { $_.Op.Fim.ToString('dd/MM') } else { '?' }))" }) -join ' | ')
            PalavrasEmComum = (($mt | ForEach-Object { $_.Comuns } | Select-Object -Unique -First 8) -join ', ')
            _Matches = $mt
        }
        $empresas += $emp
        foreach ($x in $mt) { $x.Op.Empresas += $emp }
    }
    $empresas = @($empresas | Sort-Object @{e={$_.QtdOportunidades};Descending=$true}, @{e={$_.ContratosRecentes};Descending=$true})
    Escrever "   $($empresas.Count) empresas MEI / ME / EPP encontradas ($(@($empresas | Where-Object { $_.QtdOportunidades -gt 0 }).Count) com licitação aberta parecida).`n" 'Green'
} else {
    Escrever "[4/5] Busca de empresas desligada.`n" 'DarkGray'
}

# =====================================================================
#  5) RELATÓRIOS
# =====================================================================
Escrever '[5/5] Gerando relatórios...' 'Yellow'
$ordenadas = @($ops | Sort-Object @{e={$_.Pontos};Descending=$true}, @{e={$_.Fim}})

# --- CSV de oportunidades
$csvOps = Join-Path $PastaSaida "oportunidades_$Carimbo.csv"
$ordenadas | ForEach-Object {
    [pscustomobject]@{
        'Bom pra pequeno?' = $(switch ($_.Nivel) { 'otima' {'ÓTIMA'} 'talvez' {'PODE VALER'} default {'DIFÍCIL'} })
        'Cidade' = $_.Cidade; 'Órgão' = $_.Orgao; 'O que querem' = $_.ObjetoSimples
        'Valor estimado' = $(if ($_.Valor -gt 0) { $_.Valor.ToString('N2', $BR) } else { '' })
        'Prazo proposta' = $(if ($_.Fim) { $_.Fim.ToString('dd/MM/yyyy HH:mm') } else { '' })
        'Dias restantes' = $_.DiasRestantes; 'Tipo' = $_.Modalidade
        'Itens só ME/EPP' = @($_.ItensExclusivos).Count; 'Total itens' = @($_.Itens).Count
        'Por quê' = ($_.Motivos -join '; '); 'Empresas interessadas' = @($_.Empresas).Count; 'Link' = $_.Link
    }
} | Export-Csv -Path $csvOps -NoTypeInformation -Delimiter ';' -Encoding UTF8

# --- CSV de empresas
$csvEmp = Join-Path $PastaSaida "empresas-interessadas_$Carimbo.csv"
if ($empresas.Count) {
    $empresas | Select-Object Empresa, NomeFantasia, CNPJ, Porte, Cidade, Telefone, Email, Atividade, JaVendeuPara, ContratosRecentes,
        @{n='ValorContratos';e={$_.ValorContratos.ToString('N2', $BR)}}, ExemploVenda, QtdOportunidades, Oportunidades, PalavrasEmComum |
        Export-Csv -Path $csvEmp -NoTypeInformation -Delimiter ';' -Encoding UTF8
}

# --- Mensagens prontas de WhatsApp
function Get-MsgWhats($op) {
    $t = $op.Texto -replace '(?m)^([A-ZÇÃÁÉÍÓÚÊÔ ]+):', '*$1:*'
    return "📢 *OPORTUNIDADE DE VENDA PRO GOVERNO*`n`n$t`n`n_Dúvidas? Procure a Sala do Empreendedor ou o Sebrae (0800 570 0800)._"
}
$txtWhats = Join-Path $PastaSaida "mensagens-whatsapp_$Carimbo.txt"
$blocos = $ordenadas | Where-Object { $_.Nivel -ne 'dificil' } | ForEach-Object { Get-MsgWhats $_ }
[IO.File]::WriteAllText($txtWhats, ($blocos -join "`n`n------------------------------`n`n"), $Utf8Bom)

# --- HTML
$nOtima = @($ops | Where-Object Nivel -eq 'otima').Count
$nTalvez = @($ops | Where-Object Nivel -eq 'talvez').Count
$somaValor = ($ops | Measure-Object Valor -Sum).Sum
$sb = New-Object Text.StringBuilder
$i = 0
foreach ($op in $ordenadas) {
    $i++
    $selo = switch ($op.Nivel) { 'otima' { '<span class="selo verde">✅ Ótima pra pequenos</span>' } 'talvez' { '<span class="selo amarelo">🟡 Pode valer a pena</span>' } default { '<span class="selo cinza">⚪ Mais difícil pra pequenos</span>' } }
    $urg = ''; if ($null -ne $op.DiasRestantes -and $op.DiasRestantes -le 5) { $urg = '<span class="selo vermelho">⏰ Termina logo</span>' }
    $corpo = if ($op.TextoIA) { $op.TextoIA } else { $op.Texto }
    $corpoHtml = (HtmlEnc $corpo) -replace "`r?`n", '<br>'
    $corpoHtml = [regex]::Replace($corpoHtml, '(^|<br>)([A-ZÇÃÁÉÍÓÚÊÔ ?]+[:?])', '$1<b>$2</b>')
    $iaTag = if ($op.TextoIA) { '<span class="selo azul">🤖 Resumo feito pela IA lendo o edital</span>' } else { '' }
    $exHtml = ''
    if (@($op.ItensExclusivos).Count) {
        $li = ($op.ItensExclusivos | Select-Object -First 6 | ForEach-Object { "<li>$(HtmlEnc $_.descricao) <small>($(Format-Reais $_.valorTotal))</small></li>" }) -join ''
        $mais = if (@($op.ItensExclusivos).Count -gt 6) { "<li><i>e mais $(@($op.ItensExclusivos).Count - 6)...</i></li>" } else { '' }
        $exHtml = "<details><summary>Ver itens só pra MEI/ME/EPP</summary><ul>$li$mais</ul></details>"
    }
    $empHtml = ''
    if (@($op.Empresas).Count) {
        $li = ($op.Empresas | Select-Object -First 8 | ForEach-Object { "<li><b>$(HtmlEnc $_.Empresa)</b> ($($_.Porte), $(HtmlEnc $_.Cidade)) $(HtmlEnc $_.Telefone)</li>" }) -join ''
        $empHtml = "<details><summary>🏢 $(@($op.Empresas).Count) empresa(s) pequena(s) que provavelmente se interessam</summary><ul>$li</ul></details>"
    }
    $msg = HtmlEnc (Get-MsgWhats $op)
    $busca = HtmlEnc ((Remove-Acentos ($op.Objeto + ' ' + $op.Cidade + ' ' + $op.Orgao)).ToLower())
    [void]$sb.Append(@"
<div class="card $($op.Nivel)" data-nivel="$($op.Nivel)" data-busca="$busca">
  <div class="topo">$selo $urg $iaTag <span class="cidade">📍 $(HtmlEnc $op.Cidade)</span></div>
  <h3>$(HtmlEnc $op.ObjetoSimples)</h3>
  <div class="linha"><span>💰 <b>$(Format-Reais $op.Valor)</b></span><span>📅 até <b>$(if ($op.Fim) { $op.Fim.ToString('dd/MM/yyyy HH:mm') } else { '?' })</b></span><span>🏛️ $(HtmlEnc $op.Orgao)</span></div>
  <div class="texto">$corpoHtml</div>
  $exHtml $empHtml
  <div class="acoes"><a class="btn" href="$($op.Link)" target="_blank">Ver edital no PNCP</a><button class="btn sec" onclick="copiar('m$i', this)">📋 Copiar mensagem pro WhatsApp</button></div>
  <textarea id="m$i" hidden>$msg</textarea>
</div>
"@)
}

$sbEmp = New-Object Text.StringBuilder
foreach ($e in $empresas) {
    $op1 = if ($e.QtdOportunidades) { HtmlEnc $e.Oportunidades } else { '<span class="muted">nenhuma parecida aberta agora</span>' }
    [void]$sbEmp.Append("<tr><td><b>$(HtmlEnc $e.Empresa)</b><br><small>$(HtmlEnc $e.NomeFantasia)</small></td><td><span class='porte'>$($e.Porte)</span></td><td>$(HtmlEnc $e.CNPJ)</td><td>$(HtmlEnc $e.Cidade)</td><td>$(HtmlEnc $e.Telefone)<br><small>$(HtmlEnc $e.Email)</small></td><td><small>$(HtmlEnc $e.Atividade)</small></td><td><small>$(HtmlEnc $e.ExemploVenda)</small></td><td><small>$op1</small></td></tr>")
}
$tabelaEmp = if ($empresas.Count) { "<table><thead><tr><th>Empresa</th><th>Porte</th><th>CNPJ</th><th>Cidade</th><th>Contato</th><th>Atividade</th><th>Já vendeu ao governo</th><th>Licitações abertas parecidas</th></tr></thead><tbody>$($sbEmp.ToString())</tbody></table>" } else { '<p class="muted">Busca de empresas não executada ou nenhuma encontrada.</p>' }

$html = @"
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radar de Licitações - $($Agora.ToString('dd/MM/yyyy'))</title>
<style>
 *{box-sizing:border-box} body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f3f5f9;color:#1d2433;font-size:17px;line-height:1.5}
 header{background:linear-gradient(135deg,#0b5cad,#0f8a5f);color:#fff;padding:28px 20px} header h1{margin:0 0 6px;font-size:28px} header p{margin:0;opacity:.9}
 main{max-width:1100px;margin:0 auto;padding:20px}
 .resumo{display:flex;gap:12px;flex-wrap:wrap;margin:-40px 0 20px} .box{background:#fff;border-radius:14px;padding:16px 20px;flex:1;min-width:180px;box-shadow:0 2px 10px #0001} .box b{font-size:28px;display:block}
 .explica{background:#fffbea;border:2px solid #f5d76e;border-radius:14px;padding:16px 20px;margin-bottom:20px} .explica h2{margin-top:0;font-size:20px}
 .abas{display:flex;gap:8px;margin:20px 0 10px} .aba{padding:10px 18px;border-radius:10px;border:none;background:#dde3ee;cursor:pointer;font-size:16px} .aba.on{background:#0b5cad;color:#fff}
 .filtros{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center} .filtros button{padding:8px 14px;border-radius:20px;border:1px solid #bbb;background:#fff;cursor:pointer} .filtros button.on{background:#1d2433;color:#fff}
 .filtros input{flex:1;min-width:200px;padding:9px 14px;border-radius:20px;border:1px solid #bbb;font-size:16px}
 .card{background:#fff;border-radius:14px;padding:18px 20px;margin-bottom:14px;box-shadow:0 2px 10px #0001;border-left:7px solid #ccc} .card.otima{border-left-color:#1aa260} .card.talvez{border-left-color:#f1b400}
 .card h3{margin:8px 0;font-size:20px} .topo{display:flex;gap:8px;flex-wrap:wrap;align-items:center} .cidade{margin-left:auto;color:#555}
 .selo{padding:3px 10px;border-radius:20px;font-size:14px;font-weight:600} .verde{background:#e3f7ec;color:#11703f} .amarelo{background:#fff4cc;color:#8a6500} .cinza{background:#eee;color:#555} .vermelho{background:#fde4e4;color:#b42318} .azul{background:#e5efff;color:#0b5cad}
 .linha{display:flex;gap:18px;flex-wrap:wrap;color:#333;margin-bottom:10px;font-size:15px}
 .texto{background:#f7f9fc;border-radius:10px;padding:12px 14px;font-size:16px}
 details{margin-top:10px} summary{cursor:pointer;color:#0b5cad;font-weight:600}
 .acoes{margin-top:12px;display:flex;gap:10px;flex-wrap:wrap} .btn{background:#0b5cad;color:#fff;border:none;padding:9px 16px;border-radius:10px;text-decoration:none;cursor:pointer;font-size:15px} .btn.sec{background:#1aa260}
 table{width:100%;border-collapse:collapse;background:#fff;border-radius:14px;overflow:hidden;font-size:14px} th{background:#1d2433;color:#fff;text-align:left;padding:10px} td{padding:9px 10px;border-bottom:1px solid #eee;vertical-align:top}
 .porte{background:#e3f7ec;color:#11703f;padding:2px 8px;border-radius:8px;font-weight:700} .muted{color:#888}
 .aviso{font-size:13px;color:#666;margin-top:30px;border-top:1px solid #ddd;padding-top:12px}
</style></head><body>
<header><h1>📡 Radar de Licitações para MEI e Pequenas Empresas</h1>
<p>Atualizado em $($Agora.ToString('dd/MM/yyyy')) às $($Agora.ToString('HH:mm')) · Cidades: $(($Municipios.Values) -join ', ')</p></header>
<main>
<div class="resumo">
 <div class="box"><b>$($ops.Count)</b>licitações abertas</div>
 <div class="box"><b style="color:#1aa260">$nOtima</b>ótimas pra pequenos</div>
 <div class="box"><b style="color:#c99700">$nTalvez</b>podem valer a pena</div>
 <div class="box"><b>$(if ($somaValor) { 'R$ ' + ([double]$somaValor / 1000000).ToString('N1', $BR) + ' mi' } else { '-' })</b>em compras abertas</div>
 <div class="box"><b>$($empresas.Count)</b>empresas MEI/ME/EPP mapeadas</div>
</div>
<div class="explica"><h2>🧭 Entenda em 1 minuto</h2>
 <b>Licitação</b> é quando o governo precisa comprar algo e abre uma disputa. Quem oferece o melhor preço, seguindo as regras, ganha.<br>
 <b>MEI pode participar!</b> E tem vantagem: compras de até R$ 80 mil costumam ser <b>só para MEI, ME e EPP</b>.<br>
 ✅ <b>Verde</b> = boa chance pra pequenos. 🟡 <b>Amarelo</b> = pode valer, leia com calma. ⚪ <b>Cinza</b> = grande ou complicada.<br>
 📘 Nunca participou? Leia o <a href="../guia-licitacao-simples.html" target="_blank">Guia: Licitação sem complicação</a>.
</div>
<div class="abas"><button class="aba on" onclick="aba('ops',this)">📋 Licitações abertas</button><button class="aba" onclick="aba('emp',this)">🏢 Empresas interessadas</button></div>
<section id="ops">
 <div class="filtros"><button class="on" onclick="filtrar('todas',this)">Todas</button><button onclick="filtrar('otima',this)">✅ Só as ótimas</button><button onclick="filtrar('talvez',this)">🟡 Pode valer</button><input id="busca" placeholder="🔎 Buscar: ex. limpeza, uniforme, alimento, Pinhais..." oninput="filtrar(null,null)"></div>
 $($sb.ToString())
</section>
<section id="emp" style="display:none">
 <p>Empresas <b>MEI, ME e EPP</b> que já fecharam contrato com os órgãos da região nos últimos $DiasHistoricoContratos dias, cruzadas com as licitações abertas parecidas com o que elas vendem. Dados públicos (PNCP e Receita Federal).</p>
 $tabelaEmp
</section>
<p class="aviso">Fontes: PNCP (pncp.gov.br) e Receita Federal via BrasilAPI. Este relatório é um apoio: <b>sempre confira o edital oficial</b> antes de participar. Os resumos automáticos podem conter erros.
Dados de empresas são públicos, mas use-os com responsabilidade (LGPD): contato só com finalidade legítima e respeite quem pedir pra não receber mensagens.</p>
</main>
<script>
let nivel='todas';
function filtrar(n,b){ if(n){nivel=n; document.querySelectorAll('.filtros button').forEach(x=>x.classList.remove('on')); b.classList.add('on');}
 const q=document.getElementById('busca').value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
 document.querySelectorAll('.card').forEach(c=>{ const okN = nivel==='todas'||c.dataset.nivel===nivel; const okQ=!q||c.dataset.busca.includes(q); c.style.display=(okN&&okQ)?'':'none'; }); }
function aba(id,b){ document.getElementById('ops').style.display=id==='ops'?'':'none'; document.getElementById('emp').style.display=id==='emp'?'':'none'; document.querySelectorAll('.aba').forEach(x=>x.classList.remove('on')); b.classList.add('on'); }
function copiar(id,b){ const t=document.getElementById(id).value; navigator.clipboard.writeText(t).then(()=>{ const o=b.innerText; b.innerText='✔ Copiado!'; setTimeout(()=>b.innerText=o,1500); }); }
</script>
</body></html>
"@
$arqHtml = Join-Path $PastaSaida "relatorio_$Carimbo.html"
[IO.File]::WriteAllText($arqHtml, $html, $Utf8Bom)

Escrever "`n=== PRONTO! ===" 'Cyan'
Escrever "Relatório:        $arqHtml" 'Green'
Escrever "Planilha licit.:  $csvOps" 'Green'
if ($empresas.Count) { Escrever "Planilha empresas: $csvEmp" 'Green' }
Escrever "WhatsApp:         $txtWhats`n" 'Green'
if (-not $env:RADAR_NAO_ABRIR) { Start-Process $arqHtml }
