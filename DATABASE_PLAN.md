# Adatbázis terv

## 1. products
A szer törzsadatai.

Mezők:
- termek
- rendeltetes
- hatoanyagok
- engedelyszam
- engedely_tipus
- formulacio
- forgalmi_kategoria
- kiszereles
- akg
- aop
- aop1
- aop4
- aop5
- mehveszelyesseg
- tulajdonos
- hazai_kepviselet
- forgalmazo
- gyarto
- dokumentum_url
- forras_url

## 2. product_usages
Felhasználási adatok kultúránként és károsítónként.

Mezők:
- termek
- kultura
- karosito
- dozis
- kezelesek_max_szama
- kezeles_ideje
- le_mennyiseg
- elelmezes_egeszsegugyi_varakozasi_ido
- munkaegeszsegugyi_varakozasi_ido
- forras_url

## 3. product_active_substances
Hatóanyagok és rezisztencia szempontból fontos adatok.

Mezők:
- termek
- hatoanyag_nev
- mennyiseg
- hatoanyag_csoport
- hatasmod
- rac_besorolas
- forras_url
