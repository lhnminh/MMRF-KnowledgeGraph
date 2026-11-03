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
