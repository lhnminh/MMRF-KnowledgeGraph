// Run each query separately in Neo4j Browser at http://localhost:7474/browser/.
// Use Graph view for node/path results and Table view for evidence.
// Associations and shared pathways do not by themselves establish causation.

// 1. Display the full demo graph.
MATCH p=(a:Entity)-[:RELATIONSHIP]->(b:Entity)
RETURN p
LIMIT 100;

// 2. Explore the immediate Multiple Myeloma neighborhood.
MATCH p=(d:Disease {id:'MONDO:0009693'})-[:RELATIONSHIP]-(other:Entity)
RETURN p
LIMIT 100;

// 3. Show direct and two-hop TP53 connections to Multiple Myeloma.
MATCH p=(g:Gene {name:'TP53'})-[:RELATIONSHIP*1..2]-(d:Disease {id:'MONDO:0009693'})
RETURN p
LIMIT 20;

// 4. Display the genes directly associated with Multiple Myeloma.
MATCH p=(g:Gene)-[r:RELATIONSHIP]->(d:Disease {id:'MONDO:0009693'})
WHERE r.predicate = 'biolink:associated_with'
RETURN p;

// 5. Show disease-level phenotypes, preserving each source assertion.
MATCH p=(d:Disease {id:'MONDO:0009693'})-[r:RELATIONSHIP]->(phenotype:PhenotypicFeature)
WHERE r.predicate = 'biolink:has_phenotype'
RETURN p
LIMIT 100;

// 6. Find gene pairs connected through a shared pathway.
MATCH p=(a:Gene)-[r1:RELATIONSHIP]->(pathway:Pathway)<-[r2:RELATIONSHIP]-(b:Gene)
WHERE a.id < b.id
  AND r1.predicate = 'biolink:participates_in'
  AND r2.predicate = 'biolink:participates_in'
RETURN p
LIMIT 20;

// 7. Show the TP53 gene-to-protein mapping.
MATCH p=(g:Gene {name:'TP53'})-[r:RELATIONSHIP]->(protein:Protein)
WHERE r.predicate = 'orion:encodes'
RETURN p;

// 8. Inspect evidence and provenance for the direct TP53/myeloma relationship.
MATCH (g:Gene {name:'TP53'})-[r:RELATIONSHIP]-(d:Disease {id:'MONDO:0009693'})
RETURN g.name AS gene,
       d.name AS disease,
       r.predicate AS relationship,
       r.source AS source,
       r.source_url AS source_url,
       r.evidence_json AS evidence,
       r.retrieved_at AS retrieved_at,
       r.review_status AS review_status,
       r.confidence AS confidence;

// 9. Verify the number of loaded relationships from each source.
MATCH ()-[r:RELATIONSHIP]->()
RETURN r.source AS source, count(r) AS relationships
ORDER BY source;

// 10. Explore Open Targets gene associations and their source scores.
// Scores describe the source association; they are not probabilities/confidence.
MATCH p=(g:Gene)-[r:RELATIONSHIP]->(d:Disease {id:'MONDO:0009693'})
WHERE r.predicate = 'opentargets:associated_target'
RETURN p, g.name AS gene, r.association_score AS association_score,
       r.source_url AS source_url
ORDER BY association_score DESC
LIMIT 25;

// 11. Inspect drug/candidate records and clinical-report references.
// A candidate connection does not establish approval or efficacy.
MATCH p=(drug:Drug)-[r:RELATIONSHIP]->(d:Disease {id:'MONDO:0009693'})
WHERE r.predicate = 'opentargets:drug_or_clinical_candidate'
RETURN p, drug.name AS drug, r.clinical_stage AS disease_clinical_stage,
       r.evidence_json AS clinical_reports, r.source_url AS source_url
ORDER BY drug
LIMIT 25;

// 12. Show drug mechanisms targeting a demo gene.
MATCH p=(drug:Drug)-[a:RELATIONSHIP]->(m:Mechanism)-[b:RELATIONSHIP]->(g:Gene)
WHERE a.predicate = 'opentargets:has_mechanism_of_action'
  AND b.predicate = 'opentargets:has_target'
  AND g.name IN ['TP53', 'KRAS', 'NRAS', 'BRAF']
RETURN p, a.action_type AS action_type, a.source_url AS source_url
LIMIT 30;

// 13. Show CIViC evidence in its disease, molecular-profile, variant and gene context.
// These paths preserve evidence context; they are not general gene-level efficacy claims.
MATCH p=(d:Disease {id:'MONDO:0009693'})<-[a:RELATIONSHIP]-(e:Evidence)
        -[b:RELATIONSHIP]->(mp:MolecularProfile)-[c:RELATIONSHIP]->(v:Variant)
        -[f:RELATIONSHIP]->(g:Gene)
WHERE a.predicate = 'civic:has_disease_context'
  AND b.predicate = 'civic:about_molecular_profile'
  AND c.predicate = 'civic:includes_variant'
  AND f.predicate = 'civic:variant_of'
  AND g.name IN ['TP53', 'KRAS', 'NRAS', 'BRAF']
RETURN p, e.review_status AS review_status, e.evidence_direction AS direction,
       e.significance AS significance, e.source_url AS source_url
LIMIT 30;

// 14. Read CIViC evidence, publication and mentioned-therapy links.
MATCH p=(e:Evidence)-[r:RELATIONSHIP]->(other:Entity)
WHERE r.source = 'civic'
  AND r.predicate IN ['civic:cites', 'civic:mentions_therapy']
RETURN p, e.review_status AS review_status, e.evidence_type AS evidence_type,
       e.evidence_level AS evidence_level, e.evidence_direction AS direction,
       e.significance AS significance, r.evidence_json AS evidence,
       r.source_url AS source_url
LIMIT 50;
