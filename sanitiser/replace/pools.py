"""Curated pools of fictional replacement names.

These are deliberately fictional and follow the pattern conventions of the
real entities they replace (banks have bank-like suffixes, persons have
plausible Western first/last names, etc.). Pools can be extended via config.

If a real document needs a stronger guarantee that a replacement isn't
accidentally a real entity name, the operator should supply a custom pool
file. See ``replacement.pools.set_pools(...)``.
"""
from __future__ import annotations


# Bank/financial-org-like fictional names (full + abbreviation).
# Each entry: full_name, abbreviation
ORG_BANK_POOL = [
    ("Northbridge International Banking Corporation", "NIBC"),
    ("Arden Global Banking Group", "AGBG"),
    ("Mercer Union Bank", "MUB"),
    ("Whitestone Capital Bank", "WCB"),
    ("Larkfield Trust Bank", "LTB"),
    ("Eastlake Federal Banking Corp", "EFBC"),
    ("Pinehurst Holdings Bank", "PHB"),
    ("Cotswold Reserve Bank", "CRB"),
    ("Kingsbury Mercantile Bank", "KMB"),
    ("Templeton Heritage Bank", "THB"),
    ("Branscombe National Bank", "BNB"),
    ("Marlowe Continental Bank", "MCB"),
    ("Greenfield Pacific Banking Corp", "GPBC"),
    ("Westbridge Atlantic Bank", "WAB"),
    ("Stonecroft Federal Trust", "SFT"),
    ("Foxglove Capital Bank", "FCB"),
]

# Generic corporate (non-bank) pool
ORG_CORP_POOL = [
    ("Redwood Trading", "RT"),
    ("Northgate Holdings", "NH"),
    ("Oakfield Services", "OS"),
    ("Silverline Capital", "SC"),
    ("Brookstone Consulting", "BC"),
    ("Highmark Solutions", "HS"),
    ("Larkspur Investments", "LI"),
    ("Pinehurst Advisory", "PA"),
    ("Foxglove Partners", "FP"),
    ("Ashford Strategies", "AS"),
    ("Wycombe Trading", "WT"),
    ("Branscombe Resources", "BR"),
]

PERSON_FIRST_NAMES = [
    "James", "Michael", "David", "Robert", "Thomas", "William", "Andrew",
    "Christopher", "Daniel", "Matthew", "Steven", "Mark", "Paul", "Edward",
    "Sarah", "Emma", "Olivia", "Sophia", "Charlotte", "Mia", "Isabella",
    "Amelia", "Harper", "Evelyn", "Abigail", "Emily", "Charlotte", "Eleanor",
]

PERSON_LAST_NAMES = [
    "Whitmore", "Brennan", "Carter", "Hughes", "Coleman", "Jenkins", "Morgan",
    "Foster", "Reed", "Hayes", "Bryant", "Russell", "Griffin", "Hamilton",
    "Sullivan", "Wallace", "Cole", "Bennett", "Murphy", "Graham",
    "Templeton", "Branscombe", "Cotswold", "Westbridge", "Eastlake", "Larkfield",
]

PERSON_TITLES = ["Mr", "Mrs", "Ms", "Dr"]

LOCATION_POOL = [
    "Aldershire", "Brackenford", "Cleavestown", "Drachmoor", "Embleton",
    "Falmouth Heath", "Greendale", "Hartlebury", "Ironbridge", "Junipertown",
    "Kettleford", "Lavender Bay", "Marshden", "Northvale", "Oakridge",
]
