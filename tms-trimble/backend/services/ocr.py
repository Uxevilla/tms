"""Servicios de negocio (lógica compartida; sin FastAPI)."""

import datetime
import json
import os
import secrets
import urllib.parse
import urllib.request

import config
from db import *
from core import *
from security import *
from tenancy import *
from clients.trimble import get_client
from clients.transfollow import get_transfollow_client
from clients.ptv import _ptv_route, _calc_ruta, _haversine_km
from clients.geocoding import _buscar_photon, reverse_geocode

import base64
import io
import re
import subprocess
import uuid

import psycopg2
import redis

from models import *
from config import TRANSFOLLOW_WEBHOOK_USER, TRANSFOLLOW_WEBHOOK_PASSWORD

from config import REDIS_URL, REDIS_STREAM, REDIS_CHANNEL, ACTIVITY_TYPES


def _parse_ticket(texto):
    proveedor, fecha, total = "", None, None
    lines = [l.strip() for l in texto.splitlines() if l.strip()]
    if lines:
        proveedor = lines[0][:60]
    m = re.search(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", texto)
    if m:
        fecha = _norm_fecha(m.group(0))
    m = re.search(r"(?i)(total|importe|a pagar|t\.?\s?p\.?)[^\d]{0,20}(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})", texto)
    if m:
        total = _norm_total(m.group(2))
    if total is None:
        amts = re.findall(r"\d{1,3}(?:[.,]\d{3})*[.,]\d{2}", texto)
        if amts:
            total = _norm_total(amts[-1])
    return proveedor, fecha, total


def _parse_documento(texto):
    """Extrae campos de factura/albarán: nº, CIF, base imponible, IVA."""
    num = ""
    m = re.search(r"(?i)(?:factura|albar[aá]n|fra\.?)\s*(?:n[ºo°]?\.?)?\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{2,30})", texto)
    if m:
        num = m.group(1).strip(" .:,-")
    cif = ""
    m = re.search(r"\b[A-Z]\d{7}[A-Z0-9]\b", texto)
    if not m:
        m = re.search(r"\b\d{8}[A-Z]\b", texto)
    if m:
        cif = m.group(0)
    base = None
    m = re.search(r"(?i)base\s*(?:imponible)?[^\d]{0,20}(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})", texto)
    if m:
        base = _norm_total(m.group(1))
    iva = None
    m = re.search(r"(?i)\biva\b[^\d]{0,20}(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})", texto)
    if m:
        iva = _norm_total(m.group(1))
    return num, cif, base, iva


def _pdf_a_texto(raw: bytes) -> str:
    """Extrae el texto de la capa de texto de un PDF (pdfplumber)."""
    try:
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception:
        return ""


def _regex_matricula(t):
    # Formato moderno: 1234 ABC ; fallback formato antiguo: A 1234 AB
    m = re.search(r"\b(\d{4})\s?([A-Z]{3})\b", t)
    if not m:
        m = re.search(r"\b([A-Z]{1,2})\s?(\d{4})\s?([A-Z]{2})\b", t)
    return m.group(0).replace(" ", "") if m else ""


def _regex_litros(t):
    m = re.search(r"(\d+[.,]?\d*)\s*(?:L|Lts?|Litros|litros)\b", t)
    if not m:
        return None
    return round(float(m.group(1).replace(",", ".")), 2)


def _regex_importe(t):
    m = re.search(r"(?:total|importe total|€|eur)\s*:?\s*([0-9][0-9.,]*\d)", t, re.I)
    if not m:
        return None
    s = m.group(1).replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _regex_fecha(t):
    m = re.search(r"\b(\d{2})[/.-](\d{2})[/.-](\d{4})\b", t)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


from services.contabilidad import _norm_total, _norm_fecha
