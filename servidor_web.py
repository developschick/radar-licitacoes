# -*- coding: utf-8 -*-
"""
Servidor Web - Radar de Licitações para MEI / ME / EPP
Rápido (multi-thread + cache inteligente), zero dependências externas.
"""
import json
import os
import re
import ssl
import time
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(BASE_DIR, "site")
CACHE_DIR = os.path.join(BASE_DIR, "saida", "cache_web")
os.makedirs(CACHE_DIR, exist_ok=True)

SSL_CTX = ssl.create_default_context()

MUNICIPIOS = {
    "4125506": "São José dos Pinhais",
    "4106902": "Curitiba",
    "4119152": "Pinhais",
    "4101804": "Araucária",
    "4105805": "Colombo",
    "4107652": "Fazenda Rio Grande",
    "4104204": "Campo Largo",
    "4119509": "Piraquara",
}

NICHO_TERMOS = {
    "alimentacao": ["alimento", "alimentos", "alimenticio", "alimenticios", "merenda", "generos alimenticios", "hortifruti", "carne", "panificacao", "pao", "leite", "refeicao", "refeicoes", "cesta basica", "agua mineral", "cafe", "acucar", "lanche", "buffet"],
    "limpeza": ["limpeza", "higiene", "higienizacao", "saneante", "saneantes", "desinfetante", "detergente", "papel higienico", "papel toalha", "saco de lixo", "dedetizacao", "pragas", "jardinagem", "rocada"],
    "obras": ["obra", "obras", "reforma", "manutencao predial", "engenharia", "viaria", "cimento", "tinta", "tintas", "eletrica", "hidraulica", "construcao", "pavimentacao", "serralheria", "marcenaria", "ar condicionado", "climatizacao", "vidracaria", "escavadeira"],
    "ti": ["informatica", "computador", "computadores", "notebook", "impressora", "impressoras", "toner", "software", "licenca de uso", "perifericos", "monitor", "monitores", "teclado", "servidor", "rede logica", "equipamentos eletronicos"],
    "saude": ["medico", "medicos", "hospitalar", "odontologico", "odontologicos", "medicamento", "medicamentos", "enfermagem", "laboratorial", "laboratoriais", "exames", "ambulatorial", "fisioterapia", "ortese", "protese", "opme", "cirurgico", "veterinario", "clinica", "amostras biologicas"],
    "veiculos": ["veiculo", "veiculos", "automotor", "automotivo", "pneu", "pneus", "oficina", "mecanica", "pecas automotivas", "combustivel", "lubrificante", "transporte escolar", "frete", "locacao de veiculos", "onibus", "furgão"],
    "escritorio": ["expediente", "papelaria", "grafico", "graficos", "impressos", "banner", "mobiliario", "cadeira", "armario", "material escolar", "didatico", "brinquedo"],
    "uniformes": ["uniforme", "uniformes", "camiseta", "camisetas", "tecido", "confeccao", "calcado", "epi", "epis", "luva", "bota", "toalha", "mochila"],
    "eventos": ["evento", "eventos", "sonorizacao", "iluminacao", "palco", "tenda", "fotografia", "filmagem", "seguranca desarmada", "recepcao", "treinamento"],
    "agro": ["agricola", "agricolas", "semente", "sementes", "adubo", "fertilizante", "alevino", "alevinos", "racao", "muda", "mudas", "grama", "analises quimicas", "macronutrientes", "tratorista", "tratoristas", "rural"]
}

STOPWORDS = {
    "para", "como", "mais", "pela", "pelo", "pelos", "pelas", "este", "esta", "isso",
    "registro", "preco", "precos", "aquisicao", "contratacao", "empresa", "empresas",
    "fornecimento", "prestacao", "servico", "servicos", "municipio", "secretaria",
    "municipal", "atender", "necessidades", "demanda", "eventual", "futura", "conforme",
    "edital", "anexos", "termo", "referencia", "meses", "periodo", "natureza", "comum",
    "especializada", "continuada", "destinadas", "necessarios", "unidades", "sistema"
}

MEM_CACHE = {}
CACHE_TTL = 1800  # 30 minutos


def sem_acentos(txt: str) -> str:
    if not txt:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(txt))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def http_get_json(url: str, timeout: int = 15, retries: int = 2):
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RadarMEI/2.0",
    }
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
                if resp.status == 204:
                    return None
                raw = resp.read().decode("utf-8", errors="replace").strip()
                if not raw or raw.startswith("<"):
                    if attempt < retries:
                        time.sleep(0.8 * (attempt + 1))
                        continue
                    return None
                return json.loads(raw)
        except Exception:
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
            else:
                return None
    return None


def limpar_objeto(obj: str) -> str:
    s = re.sub(r"\s+", " ", (obj or "")).strip()
    padroes = [
        r"^(o\s+)?registro de pre[cç]os?\s*(-\s*)?(para\s+|visando\s+(a\s+)?)?",
        r"^futura e eventual\s+",
        r"^eventual\s+",
        r"^(a\s+)?(contrata[cç][aã]o de empresa(s)?(\s+especializada(s)?)?\s+(para\s+(a\s+|o\s+)?|visando\s+)?)",
        r"^(a\s+)?(aquisi[cç][aã]o|fornecimento|presta[cç][aã]o)\s+(de\s+|dos\s+|das\s+|do\s+|da\s+)?",
    ]
    for _ in range(3):
        for p in padroes:
            s = re.sub(p, "", s, flags=re.IGNORECASE).strip()
    if len(s) >= 2 and s[1].islower():
        s = s[0].upper() + s[1:]
    return s or (obj or "Itens diversos")


def formatar_reais(v) -> str:
    if v is None:
        return "Valor não informado"
    try:
        val = float(v)
        s = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {s}"
    except Exception:
        return "Valor não informado"


def nome_orgao_amigavel(orgao: str, cidade: str) -> str:
    if re.match(r"^MUNIC[IÍ]PIO DE", orgao or "", flags=re.IGNORECASE) and cidade:
        return f"Prefeitura de {cidade}"
    return (orgao or "Órgão público").title()


def tem_termo_exato(texto_norm: str, termo: str) -> bool:
    padrao = r"(?<![a-z0-9])" + re.escape(termo) + r"(?![a-z0-9])"
    return bool(re.search(padrao, texto_norm))


def classificar_nicho(texto_objeto_norm: str):
    nichos = []
    for k, termos in NICHO_TERMOS.items():
        if any(tem_termo_exato(texto_objeto_norm, t) for t in termos):
            nichos.append(k)
    return nichos


CNAE_SINONIMOS_NICHO = {
    "alimentacao": ["alimento", "alimenticio", "restaurante", "lanchonete", "padaria", "panificacao", "mercearia", "minimercado", "supermercado", "hortifruti", "bebida", "refeicao", "buffet", "confeitaria", "doces", "carnes", "laticinios"],
    "limpeza": ["limpeza", "higiene", "saneante", "conservacao", "pragas", "dedetizacao", "imunizacao", "lavanderia", "paisagismo", "jardinagem", "residuos", "coleta"],
    "obras": ["obra", "construcao", "edificios", "engenharia", "arquitetura", "reforma", "instalacao eletrica", "eletrica", "hidraulica", "pintura", "alvenaria", "marcenaria", "serralheria", "esquadrias", "vidracaria", "impermeabilizacao", "terraplanagem", "pavimentacao", "ar condicionado", "climatizacao", "manutencao de maquinas", "metal", "madeira", "materiais de construcao", "ferragens"],
    "ti": ["informatica", "computador", "suporte tecnico", "software", "processamento de dados", "hospedagem", "telecomunicacoes", "rede", "impressora", "equipamentos de telefonia", "eletronicos", "automacao"],
    "saude": ["medico", "hospitalar", "odontologico", "odonto", "farmaceutico", "farmacia", "medicamento", "laboratorio", "diagnostico", "clinica", "fisioterapia", "ortopedia", "psicologia", "enfermagem", "veterinaria", "instrumentos cirurgicos"],
    "veiculos": ["veiculo", "automotor", "automotivo", "pneu", "oficina", "mecanica", "pecas", "acessorios para veiculos", "transporte rodoviario", "transporte escolar", "fretamento", "carga", "locacao de automoveis", "combustivel", "lubrificante"],
    "escritorio": ["papelaria", "escritorio", "impressao", "grafico", "grafica", "livros", "revistas", "moveis", "mobiliario", "carimbos", "comunicacao visual", "brinquedos", "artigos recreativos", "embalagens"],
    "uniformes": ["vestuario", "roupas", "confeccao", "tecido", "armarinho", "calcados", "bolsas", "equipamentos de protecao", "epi", "cama, mesa e banho", "costura"],
    "eventos": ["evento", "festas", "sonorizacao", "iluminacao", "fotografia", "filmagem", "treinamento", "ensino", "capacitacao", "consultoria", "publicidade", "promocao de vendas", "seguranca", "vigilancia", "locacao de palcos"],
    "agro": ["agricola", "agropecuaria", "sementes", "adubo", "fertilizante", "plantas", "mudas", "flores", "animais", "racao", "pesca", "aquicultura", "florestal", "ambiental"]
}

VOCACAO_CNAE_PUBLICO = {
    "alimentacao": "Atas de Registro de Preços de gêneros alimentícios, insumos de merenda escolar, padaria/panificação e fornecimento de lanches/coffee break para eventos municipais (alta recorrência em Dispensas até R$ 80 mil).",
    "limpeza": "Fornecimento parcelado de materiais de higiene, saneantes, descartáveis e serviços de conservação predial ou controle de pragas para escolas, postos de saúde e secretarias.",
    "obras": "Dispensas de engenharia (até R$ 119 mil pela Lei 14.133), manutenção predial corretiva, instalações elétricas/hidráulicas, pintura de unidades públicas e fornecimento de materiais de construção.",
    "ti": "Aquisição de equipamentos de informática, suprimentos (toners/periféricos), manutenção de computadores, cabeamento de rede e licenciamento de sistemas.",
    "saude": "Fornecimento de insumos médico-hospitalares, materiais odontológicos para UBS, reagentes, exames laboratoriais/imagem e equipamentos clínicos.",
    "veiculos": "Fornecimento de peças automotivas, pneus, manutenção mecânica da frota municipal (ambulâncias, ônibus, máquinas pesadas) e serviços de transporte/fretamento.",
    "escritorio": "Material de expediente administrativo, papelaria escolar, mobiliário (mesas, cadeiras, armários) e serviços gráficos/impressos oficiais.",
    "uniformes": "Confecção de uniformes escolares e funcionais, camisetas para campanhas municipais, calçados profissionais e Equipamentos de Proteção Individual (EPIs).",
    "eventos": "Locação de estruturas, sonorização, comunicação visual, cobertura fotográfica e serviços de treinamento/capacitação de servidores.",
    "agro": "Fornecimento de mudas, sementes, insumos rurais, análises de solo/água, rações, alevinos e serviços de apoio mecanizado agrícola."
}


def classificar_nicho_cnae(texto_norm: str):
    nichos = []
    for k, termos in CNAE_SINONIMOS_NICHO.items():
        if any(t in texto_norm for t in termos) or any(tem_termo_exato(texto_norm, t) for t in NICHO_TERMOS.get(k, [])):
            nichos.append(k)
    return nichos


def montar_resumo_simples(op: dict) -> str:
    obj_norm = sem_acentos(op["objeto"])
    eh_servico = bool(re.search(r"servi[cç]o|loca[cç][aã]o|manuten[cç][aã]o|transporte|limpeza|reforma|obra|an[aá]lise|operador|concess[aã]o", obj_norm))
    verbo = "contratar uma empresa para fazer" if eh_servico else "comprar de fornecedores"
    quem = nome_orgao_amigavel(op["orgao"], op["cidade"])
    obj_s = op["objetoSimples"]
    if len(obj_s) >= 2 and obj_s[1].islower():
        obj_s = obj_s[0].lower() + obj_s[1:]

    linhas = [
        f"O QUE VOCÊ VAI FORNECER: A {quem} precisa {verbo}: {obj_s}",
        f"QUANTO A PREFEITURA VAI PAGAR: Até {formatar_reais(op['valor'])} (valor máximo estimado pelo órgão)"
    ]
    if op.get("fimFormatado"):
        faltam = op.get("diasRestantes", 0)
        txt_dias = "termina hoje!" if faltam <= 0 else f"faltam {faltam} dias"
        linhas.append(f"PRAZO PARA ENTRAR: Enviar proposta pela internet até {op['fimFormatado']} ({txt_dias})")

    mod = sem_acentos(op.get("modalidade", ""))
    criterio = op.get("criterioJulgamento") or "Menor preço"
    crit_norm = sem_acentos(criterio)

    if "dispensa" in mod:
        linhas.append("COMO FUNCIONA A DISPUTA: Compra Direta (Dispensa Eletrônica) — processo rápido e simplificado pela internet. Vence a empresa que oferecer o melhor valor atendendo à descrição do pedido.")
    elif "desconto" in crit_norm:
        linhas.append("COMO FUNCIONA A DISPUTA: Disputa online por MAIOR DESCONTO — vence quem oferecer o maior percentual de desconto sobre a tabela oficial, cumprindo a qualidade exigida.")
    elif "tecnica" in crit_norm:
        linhas.append("COMO FUNCIONA A DISPUTA: Disputa por TÉCNICA E PREÇO — avalia tanto a qualificação técnica da proposta quanto o preço cobrado.")
    elif "pregao" in mod:
        linhas.append("COMO FUNCIONA A DISPUTA: Pregão Eletrônico pela internet — você dá lances online e vence quem oferecer o MENOR PREÇO (desde que o produto/serviço atenda ao padrão pedido e não seja abaixo do custo). Pequenas empresas têm vantagem de desempate por lei!")
    else:
        linhas.append(f"COMO FUNCIONA A DISPUTA: {op.get('modalidade', 'Licitação pública')} (Critério: {criterio}).")

    qtd_ex = op.get("qtdItensExclusivos", 0)
    qtd_tot = op.get("qtdItensTotal", 0)
    if qtd_ex > 0 and qtd_ex == qtd_tot:
        linhas.append("VANTAGEM PARA VOCÊ (MEI/ME/EPP): 100% EXCLUSIVA para pequenos negócios! Empresas grandes são proibidas por lei de entrar nessa disputa.")
    elif qtd_ex > 0:
        linhas.append(f"VANTAGEM PARA VOCÊ (MEI/ME/EPP): Tem {qtd_ex} de {qtd_tot} item(ns) separados EXCLUSIVAMENTE para MEI, ME e EPP (somando {formatar_reais(op.get('valorItensExclusivos', 0))}), sem concorrência de empresas gigantes.")
    elif op.get("valor") and op["valor"] <= 80000:
        linhas.append("VANTAGEM PARA VOCÊ (MEI/ME/EPP): Como o valor é até R$ 80 mil, pela Lei Complementar 123 essa compra é voltada exclusivamente para MEI, ME e EPP.")
    elif op.get("valor") and op["valor"] <= 250000:
        linhas.append("VANTAGEM PARA VOCÊ (MEI/ME/EPP): Porte médio — mesmo onde grandes entram, MEI/ME/EPP tem direito legal de cobrir a melhor oferta se ficar até 5% próxima do menor lance!")
    else:
        linhas.append("PORTE DA DISPUTA: Contrato grande aberto a empresas de todos os tamanhos. Para MEI sozinho pode exigir capital maior, mas MEs e EPPs estruturadas conseguem disputar.")

    if op.get("srp"):
        linhas.append("COMO É A ENTREGA (SEM APERTO): É 'Registro de Preços'. Você NÃO precisa entregar tudo de uma vez! A prefeitura trava o preço com você por 1 ano e vai pedindo aos poucos conforme precisar.")
    else:
        linhas.append("COMO É A ENTREGA: Contrato direto conforme cronograma do edital, com pagamento garantido em conta após a entrega da nota fiscal.")

    linhas.append(f"POR QUE ISSO AJUDA SEU NEGÓCIO: Você fecha uma venda grande de uma só vez para um cliente que não dá calote (verba pública reservada), aumenta seu faturamento sem gastar com anúncios e cria histórico oficial de fornecedor.")
    return "\n".join(linhas)


def montar_mensagem_whatsapp(op: dict) -> str:
    quem = nome_orgao_amigavel(op["orgao"], op["cidade"])
    obj_norm = sem_acentos(op["objeto"])
    eh_servico = bool(re.search(r"servi[cç]o|loca[cç][aã]o|manuten[cç][aã]o|transporte|limpeza|reforma|obra|an[aá]lise|operador|concess[aã]o", obj_norm))
    acao_cliente = "prestar este serviço" if eh_servico else "fornecer estes itens"

    qtd_ex = op.get("qtdItensExclusivos", 0)
    qtd_tot = op.get("qtdItensTotal", 0)
    if qtd_ex > 0 and qtd_ex == qtd_tot:
        txt_porte = "• Participação Exclusiva (LC 123/2006): Processo 100% restrito a MEI, ME e EPP. Empresas de grande porte não participam desta disputa."
    elif qtd_ex > 0:
        txt_porte = f"• Cota Reservada (LC 123/2006): Possui {qtd_ex} item(ns) exclusivos para MEI, ME e EPP ({formatar_reais(op.get('valorItensExclusivos', 0))}), sem concorrência de grandes empresas."
    elif op.get("valor") and op["valor"] <= 80000:
        txt_porte = "• Enquadramento Favorável (LC 123/2006): Pelo valor estimado até R$ 80 mil, a legislação destina a disputa prioritariamente a MEI, ME e EPP."
    else:
        txt_porte = "• Preferência Legal (LC 123/2006): Micro e pequenas empresas possuem direito legal de desempate ficto (margem de até 5% para cobrir a melhor proposta)."

    if op.get("srp"):
        txt_entrega = "• Entrega Sob Demanda (Registro de Preços): Não exige entrega integral imediata. O município registra o preço por 12 meses e solicita os itens conforme a necessidade."
    else:
        txt_entrega = "• Execução Contratual: Entrega conforme cronograma estabelecido no Termo de Referência, com pagamento via empenho público."

    criterio = sem_acentos(op.get("criterioJulgamento") or "")
    if "desconto" in criterio:
        txt_regra = "Processo 100% eletrônico. As empresas cadastram suas propostas no portal e vence quem ofertar o MAIOR DESCONTO atendendo às especificações do edital."
    else:
        txt_regra = "Processo 100% eletrônico. As empresas enviam suas propostas no sistema e vence quem ofertar o MENOR PREÇO dentro das especificações técnicas exigidas no edital."

    prazo_str = f"até *{op['fimFormatado']}*" if op.get("fimFormatado") else "conforme cronograma do edital"

    blocos = [
        f"*INFORMATIVO DE COMPRAS PÚBLICAS — {op['cidade'].upper()}*",
        f"Prezado(a) empreendedor(a), identificamos uma oportunidade de contratação pública aberta na *{quem}* compatível com a atividade da sua empresa:",
        f"*1. O QUE ESTÁ SENDO COMPRADO / CONTRATADO*\n{op['objetoSimples']}",
        f"*2. VALOR ESTIMADO E PRAZO*\n• Valor máximo estimado: *{formatar_reais(op['valor'])}*\n• Cadastro de propostas: {prazo_str}\n• Modalidade: {op.get('modalidade', 'Licitação Pública')}",
        f"*3. POR QUE VALE A PENA PARTICIPAR ({acao_cliente.upper()})*\n"
        f"• *Venda em volume garantido:* Fornecimento direto para a Administração Pública, sem gastos com publicidade ou prospecção.\n"
        f"• *Pagamento assegurado:* Recursos previamente reservados no orçamento público municipal.\n"
        f"{txt_porte}\n"
        f"{txt_entrega}",
        f"*4. COMO FUNCIONA NA PRÁTICA*\n{txt_regra} A conferência das certidões da empresa só ocorre após a definição do vencedor.",
        f"*5. ACESSO AO EDITAL OFICIAL (PNCP)*\n{op['link']}",
        f"*6. AGENDAMENTO DE CONVERSA E ORIENTAÇÃO*\nTem interesse em fornecer estes itens ou entender melhor os detalhes do pedido? Responda a esta mensagem para marcarmos uma conversa rápida (por telefone, reunião online ou presencialmente) e explicarmos todo o passo a passo."
    ]
    return "\n\n".join(blocos)


def buscar_propostas_municipio(ibge: str, data_final: str):
    nome_cid = MUNICIPIOS.get(ibge, ibge)
    encontradas = []
    for pagina in range(1, 5):
        url = f"https://pncp.gov.br/api/consulta/v1/contratacoes/proposta?dataFinal={data_final}&codigoMunicipioIbge={ibge}&pagina={pagina}&tamanhoPagina=50"
        resp = http_get_json(url, timeout=15)
        if not resp or not resp.get("data"):
            break
        for c in resp["data"]:
            org = c.get("orgaoEntidade") or {}
            unid = c.get("unidadeOrgao") or {}
            cnpj = org.get("cnpj", "")
            ano = c.get("anoCompra", "")
            seq = c.get("sequencialCompra", "")
            fim_str = c.get("dataEncerramentoProposta")
            fim_dt = None
            dias_rest = None
            fim_fmt = ""
            if fim_str:
                try:
                    fim_dt = datetime.fromisoformat(fim_str.replace("Z", ""))
                    if fim_dt < datetime.now():
                        continue
                    dias_rest = max(0, (fim_dt - datetime.now()).days)
                    fim_fmt = fim_dt.strftime("%d/%m/%Y às %H:%M")
                except Exception:
                    pass

            obj = re.sub(r"\s+", " ", (c.get("objetoCompra") or "")).strip()
            obj_simples = limpar_objeto(obj)
            val = c.get("valorTotalEstimado")
            mod = c.get("modalidadeNome") or ""
            srp = bool(c.get("srp"))
            texto_objeto = sem_acentos(obj)
            texto_busca = sem_acentos(f"{obj} {nome_cid} {org.get('razaoSocial', '')}")

            # Pre-score rápido
            pts = 0
            if "dispensa" in sem_acentos(mod):
                pts += 3
            if val is not None:
                if 0 < val <= 80000:
                    pts += 3
                elif 0 < val <= 200000:
                    pts += 1
                elif val > 1000000:
                    pts -= 2
            if srp:
                pts += 1

            encontradas.append({
                "id": c.get("numeroControlePNCP") or f"{cnpj}-{ano}-{seq}",
                "cnpj": cnpj,
                "ano": ano,
                "seq": seq,
                "orgao": org.get("razaoSocial") or "",
                "orgaoAmigavel": nome_orgao_amigavel(org.get("razaoSocial") or "", unid.get("municipioNome") or nome_cid),
                "cidade": unid.get("municipioNome") or nome_cid,
                "ibge": ibge,
                "modalidade": mod,
                "objeto": obj,
                "objetoSimples": obj_simples,
                "valor": val,
                "valorFormatado": formatar_reais(val),
                "fimIso": fim_str or "",
                "fimFormatado": fim_fmt,
                "diasRestantes": dias_rest,
                "srp": srp,
                "link": f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}",
                "linkEdital": c.get("linkSistemaOrigem") or f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}",
                "pontos": pts,
                "nichos": classificar_nicho(texto_objeto),
                "textoBusca": texto_busca,
                "qtdItensTotal": 0,
                "qtdItensExclusivos": 0,
                "valorItensExclusivos": 0.0,
                "itensExclusivos": [],
            })
        if pagina >= (resp.get("totalPaginas") or 1):
            break
    return encontradas


def enriquecer_itens_op(op: dict):
    url = f"https://pncp.gov.br/api/pncp/v1/orgaos/{op['cnpj']}/compras/{op['ano']}/{op['seq']}/itens?pagina=1&tamanhoPagina=100"
    itens = http_get_json(url, timeout=10, retries=1)
    if isinstance(itens, list) and itens:
        op["qtdItensTotal"] = len(itens)
        primeiro_crit = itens[0].get("criterioJulgamentoNome")
        if primeiro_crit:
            op["criterioJulgamento"] = primeiro_crit
        exclusivos = []
        soma_ex = 0.0
        for it in itens:
            benef_id = it.get("tipoBeneficio")
            benef_nome = sem_acentos(it.get("tipoBeneficioNome") or "")
            v_tot = float(it.get("valorTotal") or 0)
            if benef_id in (1, 2, 3) or any(k in benef_nome for k in ("exclusiv", "cota", "me/epp", "microempresa")) or (0 < v_tot <= 80000 and op.get("valor", 999999) <= 80000):
                exclusivos.append({
                    "numero": it.get("numeroItem"),
                    "descricao": (it.get("descricao") or "").strip(),
                    "valorTotal": v_tot,
                    "valorFormatado": formatar_reais(v_tot),
                    "beneficio": it.get("tipoBeneficioNome") or "Até R$ 80 mil / ME-EPP"
                })
                soma_ex += v_tot
        op["qtdItensExclusivos"] = len(exclusivos)
        op["valorItensExclusivos"] = soma_ex
        op["itensExclusivos"] = exclusivos[:10]
        if exclusivos:
            op["pontos"] += 4

    pts = op["pontos"]
    op["nivel"] = "otima" if pts >= 4 else ("talvez" if pts >= 1 else "dificil")
    op["texto"] = montar_resumo_simples(op)
    op["msgWhats"] = montar_mensagem_whatsapp(op)
    return op


def obter_licitacoes_cidades(cidades_ibge: list, forcar_atualizacao: bool = False):
    chave_cache = "ops_" + "_".join(sorted(cidades_ibge))
    agora = time.time()
    arq_cache = os.path.join(CACHE_DIR, f"{chave_cache}.json")

    if not forcar_atualizacao:
        if chave_cache in MEM_CACHE and (agora - MEM_CACHE[chave_cache]["ts"] < CACHE_TTL):
            return MEM_CACHE[chave_cache]["data"], True
        if os.path.exists(arq_cache) and (agora - os.path.getmtime(arq_cache) < CACHE_TTL):
            try:
                with open(arq_cache, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                MEM_CACHE[chave_cache] = {"ts": agora, "data": dados}
                return dados, True
            except Exception:
                pass

    data_final = (datetime.now() + timedelta(days=60)).strftime("%Y%m%d")
    todas = []

    # 1. Busca propostas em paralelo por cidade
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(buscar_propostas_municipio, ibge, data_final): ibge for ibge in cidades_ibge}
        for fut in as_completed(futs):
            try:
                res = fut.result()
                if res:
                    todas.extend(res)
            except Exception:
                pass

    # Remove duplicadas
    unicas = {}
    for op in todas:
        unicas[op["id"]] = op
    lista_ops = list(unicas.values())

    # Ordena por pré-score e enriquece os itens das top 35 em paralelo (muito mais rápido!)
    lista_ops.sort(key=lambda x: (-x["pontos"], x["valor"] or 999999999))
    para_detalhar = lista_ops[:35]
    restante = lista_ops[35:]

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(enriquecer_itens_op, op) for op in para_detalhar]
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception:
                pass

    for op in restante:
        op["nivel"] = "otima" if op["pontos"] >= 4 else ("talvez" if op["pontos"] >= 1 else "dificil")
        op["texto"] = montar_resumo_simples(op)
        op["msgWhats"] = montar_mensagem_whatsapp(op)

    lista_ops.sort(key=lambda x: (-x["pontos"], x["diasRestantes"] if x["diasRestantes"] is not None else 999))

    MEM_CACHE[chave_cache] = {"ts": agora, "data": lista_ops}
    try:
        with open(arq_cache, "w", encoding="utf-8") as f:
            json.dump(lista_ops, f, ensure_ascii=False)
    except Exception:
        pass

    return lista_ops, False


def consultar_brasilapi(forn: dict):
    cnpj = forn["cnpj"]
    arq_emp = os.path.join(CACHE_DIR, f"cnpj_{cnpj}.json")
    rf = None
    if os.path.exists(arq_emp):
        try:
            with open(arq_emp, "r", encoding="utf-8") as f:
                rf = json.load(f)
        except Exception:
            rf = None
    if not rf:
        rf = http_get_json(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}", timeout=8, retries=1)
        if rf:
            try:
                with open(arq_emp, "w", encoding="utf-8") as f:
                    json.dump(rf, f, ensure_ascii=False)
            except Exception:
                pass
    if not rf:
        return None

    porte_raw = sem_acentos(str(rf.get("porte") or rf.get("descricao_porte") or ""))
    razao_norm = sem_acentos(str(rf.get("razao_social") or forn.get("nome") or ""))
    nat_jur = str(rf.get("codigo_natureza_juridica") or "")
    cap_soc = float(rf.get("capital_social") or 0)
    mei = bool(rf.get("opcao_pelo_mei"))
    simples = bool(rf.get("opcao_pelo_simples"))
    eh_pequena = mei or simples or ("micro" in porte_raw) or ("pequeno" in porte_raw) or (porte_raw in ("01", "03", "1", "3"))
    if not eh_pequena:
        return None

    # Identifica MEI tanto pela flag explícita quanto pelo padrão de Empresário Individual (213-5) de baixo capital / CPF na razão social
    padrao_mei = bool(re.search(r"(^\d{2}\.?\d{3}\.?\d{3}\s+|\d{9,11}$|\bmei\b)", razao_norm)) or (
        nat_jur == "2135" and cap_soc <= 30000 and "ltda" not in razao_norm and "eireli" not in razao_norm and "pequeno" not in porte_raw
    )
    if mei or padrao_mei:
        porte_label = "MEI"
    elif "pequeno" in porte_raw or porte_raw in ("03", "3", "epp"):
        porte_label = "EPP"
    else:
        porte_label = "ME"

    tel1 = re.sub(r"\D", "", str(rf.get("ddd_telefone_1") or ""))
    if len(tel1) >= 10:
        tel_fmt = f"({tel1[:2]}) {tel1[2:-4]}-{tel1[-4:]}"
    else:
        tel_fmt = tel1

    cnae_cod = str(rf.get("cnae_fiscal") or "").strip()
    cnae_principal = (rf.get("cnae_fiscal_descricao") or "").strip()
    cnaes_sec_lista = []
    for c in (rf.get("cnaes_secundarios") or []):
        cod_s = str(c.get("codigo") or "").strip()
        desc_s = (c.get("descricao") or "").strip()
        if desc_s and cod_s not in ("0", "0000000", ""):
            cnaes_sec_lista.append({"codigo": cod_s, "descricao": desc_s})
    secundarios_desc = [c["descricao"] for c in cnaes_sec_lista[:15]]
    texto_emp = sem_acentos(f"{cnae_principal} {' '.join(secundarios_desc)} {forn.get('exemploObjeto', '')}")

    return {
        "cnpj": cnpj,
        "empresa": rf.get("razao_social") or forn.get("nome") or "",
        "nomeFantasia": rf.get("nome_fantasia") or "",
        "porte": porte_label,
        "cidade": f"{rf.get('municipio', '')}/{rf.get('uf', '')}".strip("/"),
        "telefone": tel_fmt,
        "telefoneLimpo": tel1,
        "email": str(rf.get("email") or "").lower(),
        "cnaeCodigo": cnae_cod,
        "atividade": cnae_principal,
        "cnaesSecundarios": cnaes_sec_lista[:15],
        "qtdCnaesSecundarios": len(cnaes_sec_lista),
        "exemploVenda": forn.get("exemploObjeto") or "",
        "valorContrato": formatar_reais(forn.get("valor")),
        "nichos": classificar_nicho_cnae(texto_emp),
        "textoBusca": texto_emp,
    }


ORGAOS_RMC_PADRAO = [
    "76105543000135",  # Prefeitura de São José dos Pinhais
    "77778678000190",  # Câmara Municipal de São José dos Pinhais
    "76417005000186",  # Prefeitura de Curitiba
    "95423000000100",  # Prefeitura de Pinhais
    "76105535000199",  # Prefeitura de Araucária
    "76105634000170",  # Prefeitura de Colombo
    "95422986000102",  # Prefeitura de Fazenda Rio Grande
    "76105618000188",  # Prefeitura de Campo Largo
    "76105568000139",  # Prefeitura de Piraquara
]

TERMOS_EXCLUIR_GRANDE = (
    " s/a", " s.a", "companhia ", "banco ", "universidade", "associacao",
    "fundacao", "hospital ", "consorcio", "instituto ", "telefonica",
    "copel ", "sanepar", "petrobras", "correios", "caixa economica"
)


def _coletar_contratos_pagina(cnpj_org: str, dt_ini: str, dt_fim: str, pag: int):
    url = f"https://pncp.gov.br/api/consulta/v1/contratos?dataInicial={dt_ini}&dataFinal={dt_fim}&cnpjOrgao={cnpj_org}&pagina={pag}&tamanhoPagina=50"
    r = http_get_json(url, timeout=12, retries=1)
    if not r or not r.get("data"):
        return []
    achados = []
    for ct in r["data"]:
        ni = re.sub(r"\D", "", str(ct.get("niFornecedor") or ""))
        nome = (ct.get("nomeRazaoSocialFornecedor") or "").strip()
        nome_norm = " " + sem_acentos(nome) + " "
        if len(ni) != 14 or any(t in nome_norm for t in TERMOS_EXCLUIR_GRANDE):
            continue
        val = ct.get("valorGlobal") or ct.get("valorInicial") or 0
        # Prioriza contratos de menor/médio valor ou com cara de MEI/EPP (Pessoa física c/ CNPJ, ME, EPP, LTDA)
        eh_provavel_mei = bool(re.search(r"(^\d{2}\.?\d{3}\.?\d{3}|\d{9,11}$|\bmei\b|\bepp\b)", sem_acentos(nome))) or ("ltda" not in nome_norm)
        achados.append({
            "cnpj": ni,
            "nome": nome,
            "exemploObjeto": limpar_objeto(ct.get("objetoContrato") or ""),
            "valor": val,
            "prioridade": 0 if eh_provavel_mei else (1 if (val and float(val) <= 250000) else 2)
        })
    return achados


CIDADES_RMC_NOMES = (
    "SAO JOSE DOS PINHAIS", "SÃO JOSÉ DOS PINHAIS", "CURITIBA",
    "PINHAIS", "ARAUCARIA", "ARAUCÁRIA", "COLOMBO",
    "FAZENDA RIO GRANDE", "CAMPO LARGO", "PIRAQUARA"
)


def _coletar_fornecedores_comprasgov(args):
    pid, nj, pag = args
    extra = f"&naturezaJuridicaId={nj}" if nj else ""
    url = f"https://dadosabertos.compras.gov.br/modulo-fornecedor/1_consultarFornecedor?pagina={pag}&tamanhoPagina=500&ativo=true&porteEmpresaId={pid}{extra}"
    r = http_get_json(url, timeout=12, retries=1)
    if not r or not r.get("resultado"):
        return []
    achados = []
    for x in r["resultado"]:
        if x.get("ufSigla") != "PR" or not x.get("habilitadoLicitar"):
            continue
        ni = re.sub(r"\D", "", str(x.get("cnpj") or ""))
        if len(ni) != 14:
            continue
        mun_up = (x.get("nomeMunicipio") or "").upper()
        eh_rmc = any(c in mun_up for c in CIDADES_RMC_NOMES)
        eh_sjp = "JOSE DOS PINHAIS" in sem_acentos(mun_up).upper()
        cnae_nome = x.get("nomeCnae") or "Fornecimento de bens e serviços ao Poder Público"
        achados.append({
            "cnpj": ni,
            "nome": x.get("nomeRazaoSocialFornecedor") or "",
            "exemploObjeto": f"Fornecedor Habilitado SICAF/Compras.gov — {cnae_nome}",
            "valor": 0,
            "forcarPorte": "MEI" if nj == 2135 else ("EPP" if pid == 3 else "ME"),
            "prioridade": 0 if eh_sjp else (1 if eh_rmc else 2)
        })
    return achados


def buscar_empresas_regiao(cidades_ibge: list, ops_atuais: list):
    chave = "emp_v2_rmc"
    agora = time.time()
    arq_cache = os.path.join(CACHE_DIR, f"{chave}.json")

    if chave in MEM_CACHE and len(MEM_CACHE[chave]["data"]) >= 50 and (agora - MEM_CACHE[chave]["ts"] < CACHE_TTL * 4):
        empresas = MEM_CACHE[chave]["data"]
    elif os.path.exists(arq_cache) and os.path.getsize(arq_cache) > 5000 and (agora - os.path.getmtime(arq_cache) < CACHE_TTL * 4):
        with open(arq_cache, "r", encoding="utf-8") as f:
            empresas = json.load(f)
        MEM_CACHE[chave] = {"ts": agora, "data": empresas}
    else:
        fornecedores = {}

        # 1. Carrega contratos locais já salvos de São José dos Pinhais (emp_4125506.json) e cache CNPJ local
        arq_sjp = os.path.join(CACHE_DIR, "emp_4125506.json")
        hist_sjp = {}
        if os.path.exists(arq_sjp):
            try:
                for e in json.load(open(arq_sjp, "r", encoding="utf-8")):
                    hist_sjp[e["cnpj"]] = e
            except Exception:
                pass

        for nome_arq in os.listdir(CACHE_DIR):
            if nome_arq.startswith("cnpj_") and nome_arq.endswith(".json"):
                ni = nome_arq.replace("cnpj_", "").replace(".json", "")
                if len(ni) == 14:
                    ant = hist_sjp.get(ni, {})
                    fornecedores[ni] = {
                        "cnpj": ni,
                        "nome": ant.get("empresa", ""),
                        "exemploObjeto": ant.get("exemploVenda") or "Contrato homologado na Administração Pública Municipal (RMC)",
                        "valor": 54000,
                        "valorFormatadoFixo": ant.get("valorContrato"),
                        "prioridade": 0
                    }

        # 2. Busca ampla na base oficial de Fornecedores Habilitados (Compras.gov.br / SICAF) focando em MEI (2135) e EPP (porte 3) no PR/RMC
        tarefas_cg = (
            [(1, 2135, p) for p in range(1, 10)] +  # MEIs habilitados em licitação
            [(3, None, p) for p in range(1, 10)] +  # EPPs habilitadas em licitação
            [(1, None, p) for p in range(1, 5)]     # MEs habilitadas em licitação
        )
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(_coletar_fornecedores_comprasgov, t) for t in tarefas_cg]
            for fut in as_completed(futs):
                try:
                    for item in fut.result():
                        ni = item["cnpj"]
                        if ni not in fornecedores:
                            fornecedores[ni] = item
                except Exception:
                    pass

        # Seleciona priorizando SJP e Região Metropolitana de Curitiba (até 150 fornecedores)
        lista_forn = sorted(fornecedores.values(), key=lambda x: x["prioridade"])[:150]
        empresas = []
        with ThreadPoolExecutor(max_workers=12) as ex:
            futs = {ex.submit(consultar_brasilapi, f): f for f in lista_forn}
            for fut in as_completed(futs):
                f_orig = futs[fut]
                try:
                    res = fut.result()
                    if res:
                        if f_orig.get("forcarPorte") in ("MEI", "EPP"):
                            res["porte"] = f_orig["forcarPorte"]
                        if f_orig.get("valorFormatadoFixo"):
                            res["valorContrato"] = f_orig["valorFormatadoFixo"]
                        elif not f_orig.get("valor"):
                            res["valorContrato"] = "Habilitado SICAF"
                        empresas.append(res)
                except Exception:
                    pass

        MEM_CACHE[chave] = {"ts": agora, "data": empresas}
        try:
            with open(arq_cache, "w", encoding="utf-8") as f:
                json.dump(empresas, f, ensure_ascii=False)
        except Exception:
            pass

    # Cruza os CNAEs (Principal + todos os Secundários) com as licitações abertas
    resultado = []
    for emp in empresas:
        lista_cnaes = []
        if emp.get("atividade"):
            lista_cnaes.append({
                "tipo": "Principal",
                "codigo": emp.get("cnaeCodigo") or "",
                "descricao": emp["atividade"],
                "norm": sem_acentos(emp["atividade"]),
                "nichos": classificar_nicho_cnae(sem_acentos(emp["atividade"]))
            })
        for cs in (emp.get("cnaesSecundarios") or []):
            d_sec = cs.get("descricao") or ""
            if d_sec:
                lista_cnaes.append({
                    "tipo": "Secundário",
                    "codigo": cs.get("codigo") or "",
                    "descricao": d_sec,
                    "norm": sem_acentos(d_sec),
                    "nichos": classificar_nicho_cnae(sem_acentos(d_sec))
                })

        # Avalia cada licitação aberta contra cada CNAE individual da empresa para descobrir o melhor encaixe
        candidatos_op = []
        for op in ops_atuais:
            melhor_pts_op = 0
            melhor_cnae_op = None
            razao_match = ""
            nichos_op = set(op.get("nichos", []))

            for c_item in lista_cnaes:
                pts = 0
                motivos = []
                em_comum = set(c_item["nichos"]) & nichos_op
                if em_comum:
                    pts += 12
                    motivos.append("mesmo segmento econômico")

                palavras_cnae = [w for w in re.split(r"\W+", c_item["norm"]) if len(w) >= 4 and w not in STOPWORDS]
                termos_batidos = [p for p in palavras_cnae if tem_termo_exato(op["textoBusca"], p)]
                if termos_batidos:
                    pts += 15 * len(termos_batidos)
                    motivos.append(f"atividade direta ({', '.join(termos_batidos[:2])})")

                if pts > 0:
                    if c_item["tipo"] == "Principal":
                        pts += 3
                    if op.get("nivel") == "otima":
                        pts += 4
                    if pts > melhor_pts_op:
                        melhor_pts_op = pts
                        melhor_cnae_op = c_item
                        razao_match = " + ".join(motivos)

            if melhor_pts_op > 0 and melhor_cnae_op:
                candidatos_op.append((melhor_pts_op, op, melhor_cnae_op, razao_match))

        candidatos_op.sort(key=lambda x: (-x[0], x[1].get("diasRestantes") if x[1].get("diasRestantes") is not None else 999))

        matches = []
        for pts, op, c_usado, _ in candidatos_op[:4]:
            tag_cnae = f"[CNAE {c_usado['tipo']}]"
            matches.append(f"{op['objetoSimples'][:62]} ({op['cidade']} — {op['valorFormatado']}) {tag_cnae}")

        # Define qual é o CNAE de maior potencial público da empresa e o que fica mais legal trabalhar com ele
        if candidatos_op:
            _, op_top, cnae_top, motivo_top = candidatos_op[0]
        else:
            op_top = None
            # Escolhe o primeiro CNAE (Principal ou Secundário) que tenha vocação mapeada para licitações
            cnae_top = lista_cnaes[0] if lista_cnaes else {"tipo": "Principal", "codigo": "", "descricao": emp.get("atividade", "Comércio e Serviços"), "nichos": []}
            for c_item in lista_cnaes:
                if c_item["nichos"]:
                    cnae_top = c_item
                    break

        nichos_emp_fallback = emp.get("nichos") or []
        nicho_principal_cnae = cnae_top["nichos"][0] if cnae_top.get("nichos") else (nichos_emp_fallback[0] if nichos_emp_fallback else None)
        vocacao_texto = VOCACAO_CNAE_PUBLICO.get(
            nicho_principal_cnae,
            "Dispensas Eletrônicas de pronta entrega (até R$ 80 mil exclusivas para MEI/ME/EPP) e Atas de Registro de Preços para fornecimento parcelado ao município."
        )

        rotulo_cnae_top = f"CNAE {cnae_top['tipo']}" + (f" ({cnae_top['codigo']})" if cnae_top.get("codigo") else "") + f": {cnae_top['descricao']}"
        qtd_sec = emp.get("qtdCnaesSecundarios") or 0

        copia = dict(emp)
        copia["oportunidadesMatch"] = matches
        copia["qtdMatch"] = len(candidatos_op)
        copia["cnaeAnalisado"] = rotulo_cnae_top
        copia["vocacaoCnae"] = vocacao_texto
        copia["cnaesSecundarios"] = [
            (f"{cs.get('codigo')} - {cs.get('descricao')}" if isinstance(cs, dict) and cs.get("codigo") else (cs.get("descricao") if isinstance(cs, dict) else str(cs)))
            for cs in (emp.get("cnaesSecundarios") or [])
        ]

        nome_exibicao = emp.get("nomeFantasia") or emp.get("empresa") or "Empreendedor(a)"
        info_sec_str = f" (além de outros {qtd_sec} CNAE(s) secundário(s) do seu CNPJ)" if qtd_sec > 0 else ""

        if op_top:
            prazo0 = f"até {op_top['fimFormatado']}" if op_top.get("fimFormatado") else "em aberto"
            msg_convite = (
                f"Olá, responsável pela *{nome_exibicao}* (CNPJ {emp['cnpj']}), tudo bem?\n\n"
                f"Fizemos uma análise dos CNAEs registrados no seu CNPJ{info_sec_str} e vimos que a sua empresa possui habilitação em:\n"
                f"• *{rotulo_cnae_top}*\n\n"
                f"*O QUE MAIS COMBINA COM ESSE CNAE EM LICITAÇÕES:*\n"
                f"{vocacao_texto}\n\n"
                f"*LICITAÇÃO ABERTA QUE MAIS ENCAIXA NO SEU CNAE AGORA:*\n"
                f"• *Órgão / Município:* {op_top['orgaoAmigavel']} ({op_top['cidade']})\n"
                f"• *O que está sendo comprado:* {op_top['objetoSimples']}\n"
                f"• *Valor estimado:* {op_top['valorFormatado']}\n"
                f"• *Prazo para propostas:* {prazo0}\n"
                f"• *Edital oficial:* {op_top['link']}\n\n"
                f"Como a sua empresa é enquadrada como *{emp['porte']}* (LC 123/2006), esse CNAE permite participar com preferência legal. Podemos agendar uma conversa rápida esta semana para apresentarmos os itens e alinharmos como você pode fornecer?"
            )
        else:
            msg_convite = (
                f"Olá, responsável pela *{nome_exibicao}* (CNPJ {emp['cnpj']}), tudo bem?\n\n"
                f"Fizemos um levantamento dos CNAEs registrados no seu CNPJ{info_sec_str} e destacamos a atividade:\n"
                f"• *{rotulo_cnae_top}*\n\n"
                f"*QUAL TIPO DE LICITAÇÃO MAIS COMBINA COM O SEU CNAE:*\n"
                f"{vocacao_texto}\n\n"
                f"Como a sua empresa já possui porte *{emp['porte']}* (com benefícios de exclusividade até R$ 80 mil pela LC 123/2006), gostaríamos de agendar uma conversa rápida para mostrar como aproveitar esse CNAE nas próximas compras e dispensas da prefeitura.\n\n"
                f"Qual o melhor horário para conversarmos rapidamente por telefone ou presencialmente?"
            )
        copia["msgConvite"] = msg_convite
        resultado.append(copia)

    resultado.sort(key=lambda x: (-x["qtdMatch"], x["porte"]))
    return resultado


class RadarHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, data: dict, status: int = 200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/licitacoes":
            cidades_str = (qs.get("cidades", ["4125506"])[0] or "4125506").strip()
            cidades = [c.strip() for c in cidades_str.split(",") if c.strip() in MUNICIPIOS] or ["4125506"]
            nicho = (qs.get("nicho", ["todos"])[0] or "todos").strip().lower()
            busca = sem_acentos(qs.get("q", [""])[0] or "").strip()
            apenas_pequenos = (qs.get("pequenos", ["0"])[0] == "1")
            atualizar = (qs.get("refresh", ["0"])[0] == "1")

            t0 = time.time()
            ops, do_cache = obter_licitacoes_cidades(cidades, forcar_atualizacao=atualizar)
            filtradas = []
            for op in ops:
                if nicho and nicho != "todos":
                    termos_nicho = NICHO_TERMOS.get(nicho, [nicho])
                    if nicho not in op.get("nichos", []) and not any(t in op["textoBusca"] for t in termos_nicho):
                        continue
                if busca:
                    termos_q = [t for t in busca.split() if t]
                    if not all(t in op["textoBusca"] for t in termos_q):
                        continue
                if apenas_pequenos and op.get("nivel") == "dificil":
                    continue
                filtradas.append(op)

            duracao = round(time.time() - t0, 2)
            return self.send_json({
                "ok": True,
                "doCache": do_cache,
                "tempoSegundos": duracao,
                "total": len(filtradas),
                "totalSemFiltro": len(ops),
                "otimas": sum(1 for x in filtradas if x.get("nivel") == "otima"),
                "talvez": sum(1 for x in filtradas if x.get("nivel") == "talvez"),
                "somaValor": sum(float(x.get("valor") or 0) for x in filtradas),
                "licitacoes": filtradas,
            })

        if path == "/api/empresas":
            cidades_str = (qs.get("cidades", ["4125506"])[0] or "4125506").strip()
            cidades = [c.strip() for c in cidades_str.split(",") if c.strip() in MUNICIPIOS] or ["4125506"]
            nicho = (qs.get("nicho", ["todos"])[0] or "todos").strip().lower()
            busca = sem_acentos(qs.get("q", [""])[0] or "").strip()

            ops, _ = obter_licitacoes_cidades(cidades, forcar_atualizacao=False)
            empresas = buscar_empresas_regiao(cidades, ops)
            filtradas = []
            for e in empresas:
                if nicho and nicho != "todos":
                    termos_nicho = NICHO_TERMOS.get(nicho, [nicho])
                    if nicho not in e.get("nichos", []) and not any(t in e["textoBusca"] for t in termos_nicho):
                        continue
                if busca:
                    if not all(t in e["textoBusca"] or t in sem_acentos(e["empresa"]) for t in busca.split()):
                        continue
                filtradas.append(e)

            return self.send_json({
                "ok": True,
                "total": len(filtradas),
                "empresas": filtradas
            })

        # Servir arquivos estáticos da pasta site/
        if path == "/" or path == "":
            arq = os.path.join(SITE_DIR, "index.html")
        else:
            rel = path.lstrip("/").replace("/", os.sep)
            arq = os.path.join(SITE_DIR, rel)

        if os.path.exists(arq) and os.path.isfile(arq):
            ext = os.path.splitext(arq)[1].lower()
            ctype = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
            }.get(ext, "application/octet-stream")
            with open(arq, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404)
            self.end_headers()


def main():
    port = int(os.environ.get("RADAR_PORT", "8080"))
    server = ThreadingHTTPServer(("127.0.0.1", port), RadarHandler)
    url = f"http://localhost:{port}"
    print("=" * 62)
    print(f"  RADAR DE LICITACOES WEB RODANDO EM: {url}")
    print("=" * 62)
    if os.environ.get("RADAR_NAO_ABRIR") != "1":
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")


if __name__ == "__main__":
    main()
