---
name: dns-change-hygiene
description: How to change DNS safely — lowering the TTL before a migration, why a CNAME never sits at the apex, CAA, the shapes of SPF, DKIM and DMARC records, and how to roll back. Use when planning or reviewing a change to a zone.
---

# Changing DNS safely

## 1. Read the zone, then change the least

Read the records of the name, and of the names it points to, before deciding anything. Change one name at a
time. The smallest change that does the job is the one that is easiest to review and to undo.

## 2. TTL: the old answer lives as long as the old TTL

Resolvers keep an answer for the TTL it carried when they fetched it. After you change a record, some
of them keep serving the old value for up to the **old** TTL, whatever TTL the new record has.

Planned migration, in order:

1. **Lower the TTL** of the record to 300 seconds (5 minutes), or to the provider's minimum if it is higher
   (`plan_changes` warns; some providers refuse anything under 600 s). Publish. Nothing else changes yet.
2. **Wait one old TTL** (a record at 86400 s needs a day), so that every cache holds the short TTL.
3. **Change the target.** Publish. The old target is out of the answers within 5 minutes; keep it running
   until then.
4. **Check** the new target answers. Keep the short TTL for a day.
5. **Raise the TTL** again (3600 s is a common value) and publish.

For an emergency fix there is no time to wait: change it, say that some resolvers will keep the old answer
for up to the old TTL, and do not promise otherwise. Do not set a TTL of 0 hoping for "no caching": a TTL of
0 leaves the provider's default.

## 3. Record shapes

| Need | Record | Notes |
|:--|:--|:--|
| A name to an IPv4 address | `A` | one record per address for several servers |
| A name to an IPv6 address | `AAAA` | |
| A name to another name | `CNAME` | one per name, **alone** at its name; target absolute with a final dot (`lb.example.com.`) |
| The zone itself (apex) to another name | not a CNAME | use A/AAAA, or the provider's alias feature if it has one |
| Mail for the domain | `MX` | `10 mail.example.com.`: a priority, then a host that has an A/AAAA record, never a CNAME |
| Which servers may send mail | `TXT` at the apex | exactly **one** SPF record: `v=spf1 include:_spf.mail.example.net -all`; a second one breaks SPF |
| DKIM key | `TXT` at `<selector>._domainkey` | the key is long and may be split in several strings: keep the provider's text as given |
| DMARC policy | `TXT` at `_dmarc` | start with `v=DMARC1; p=none; rua=mailto:dmarc@example.com`, move to `quarantine` then `reject` once the reports are clean |
| Which authorities may issue certificates | `CAA` at the apex | `0 issue "letsencrypt.org"`; an empty set allows every authority |

- **A CNAME at the apex is forbidden**: the apex holds the SOA and NS records, and a CNAME cannot coexist
  with any other record. The same holds at any name that has other records (an MX, a TXT).
- Wildcards (`*`) match names that hold no record of their own; they never match a name that has one.
- Write host names absolute (with the final dot). A name without it may be read as relative to the zone.

## 4. Dangling records

A CNAME, MX or SRV record whose target is a name in the zone that holds nothing is a candidate for removal
(or for a subdomain takeover when the target is a third-party service). From the zone alone you cannot tell
whether a target **outside** the zone still exists, nor whether an address is still in use: ask the owner,
or check with the system that owns it, before deleting.

## 5. Rollback

Before every change, write down the previous values (type, name, value, TTL). To undo: put them back and
publish. A delete is undone by recreating the record. Some providers hold changes in the zone until `publish`
(the plan ends with it, and `get_provider` says so): an unpublished mistake has not reached the public yet, so
say so and ask for the correction before the publish. Others apply each change at once: there the
compensation is the only undo, and `publish` does nothing.

## 6. Order of a change

1. Plan (no write). 2. Review by the zone's owner. 3. Records: deletes, updates, creates. 4. One publish (only if
the provider stages changes), at the end. 5. Check the zone's state.
