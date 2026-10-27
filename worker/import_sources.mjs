// Import data/master_source_list.csv into Worker KV as sources:all (JSON).
// Usage: node worker/import_sources.mjs ../data/master_source_list.csv > sources.json
//        wrangler kv key put --binding KV "sources:all" --path sources.json
// Only rows you have verified (robots + terms) should get terms_status=verified
// and a verified_bidding_url before production collection — edit the CSV first.
import fs from "node:fs";
const csv = fs.readFileSync(process.argv[2], "utf8").replace(/^\uFEFF/, "");
const [head, ...lines] = csv.split(/\r?\n/).filter(Boolean);
const cols = head.split(",");
const rows = lines.map(l => {
  const cells = l.split(","); // master list has no quoted commas in v1 fields except notes; keep parser simple & document it
  const o = {}; cols.forEach((c, i) => o[c] = cells[i] || "");
  o.terms_status = o.terms_status || "unverified";
  o.framework = o.framework_guess_by_url || "";
  return o;
});
console.log(JSON.stringify(rows));
