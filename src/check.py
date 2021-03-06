#!/usr/bin/python
# -*- coding: utf-8 -*-
from RDFClosure import DeductiveClosure, RDFS_Semantics
from rdflib import Graph, URIRef, RDF
import requests
from SPARQLWrapper import SPARQLWrapper, JSON
from glob import glob
import traceback
import argparse
import os
import csv
import md5
import codecs

endpoint = "http://localhost:5820/grade/query"
repository_url = "http://localhost:5820/grade"


def sparql(query, reasoning='false'):
    headers = {
        'Accept': 'application/sparql-results+json',
    }

    params = {
        'query': query,
        'reasoning': reasoning
    }

    # print('Query:\n{}'.format(query))

    print(('Querying endpoint {}'.format(endpoint)))

    try:
        response = requests.get(endpoint, params=params, headers=headers)

        print('Results were returned, yay!')

        try:
            bindings = response.json()['results']['bindings']
            return bindings
        except:
            print("Something went wrong, says Stardog")
            print(response.status_code)
            print(response.content)
            return -1
    except:
        print('Something went wrong')
        print(query)
        print(traceback.format_exc())
        return -1


def update(data, action='add'):
    transaction_begin_url = repository_url + "/transaction/begin"
    print(('Doing {} by POST of your data to {}'.format(action, transaction_begin_url)))

    # Start the transaction, and get a transaction_id
    response = requests.post(transaction_begin_url, headers={'Accept': 'text/plain'})
    transaction_id = response.content
    print((response.status_code))

    # POST the data to the transaction
    post_url = repository_url + "/" + transaction_id + "/" + action
    print('Assuming your data is Turtle!!')
    response = requests.post(post_url, data=data, headers={'Accept': 'text/plain', 'Content-type': 'text/turtle'})
    print((response.status_code))
    print((response.content))
    # print(response.headers)

    if response.status_code != 200:
        return str(response.content)

    # Close the transaction
    transaction_close_url = repository_url + "/transaction/commit/" + transaction_id
    response = requests.post(transaction_close_url)
    print((response.status_code))
    print((response.content))
    # print(response.headers)

    if response.status_code != 200:
        return str(response.content)
    else:
        return "Ok!"


def check(path):

    rdfs_graph = Graph()
    rdfs_graph.load('rdf-schema.ttl', format='turtle')

    rdfs_nodes = list(rdfs_graph.all_nodes())

    with open('{}/grading.csv'.format(path), 'w') as out:
        fieldnames = ['Username','Assignment 2b |846597','Assignment 2c |846599','query','syntax','asserted','inferred','baseline','subjects_objects','predicates','inferred through schema','hash']
        fieldnames += [os.path.basename(fn) for fn in glob('../constraints/*.rq')]

        writer = csv.DictWriter(out, fieldnames)
        writer.writeheader()

        for f in glob("{}/*.ttl".format(path)):

            (basename, ext) = os.path.splitext(os.path.basename(f))

            basename = basename.split('_')[-1]
            line = {'Username': basename}

            print "==========\n{}\n==========".format(basename)

            try:
                with open(f, 'r') as fi:
                    contents = fi.readlines()
                    h = md5.new()
                    h.update(''.join(contents))
                    hexdigest = h.hexdigest()
            except:
                traceback.print_exc()
                print "Could not create hash of {}".format(f)

            g = Graph()
            try:
                g.load(f, format='turtle')
                line['syntax'] = 1
            except Exception:

                print "Could not parse {}".format(f)
                line['syntax'] = 0
                print(traceback.format_exc())

            # Baseline is:
            # 1. for every *new* subject, predicate or object that is a URIRef,
            #    a new triple is generated by inference rule 1
            # 2. for every *new* predicate, one additional triple is produced (subproperty of itself)
            # 3. for rdf:Property 2 more triples that define it.
            # 4. for rdf:type 2 more triples

            subjects_objects = len(set([so for so in [so for so in g.all_nodes() if type(so) == URIRef] if so not in rdfs_nodes]))
            predicates = len(set([p for p in g.predicates()]))
            baseline = subjects_objects + 2*predicates + 2 + 2

            # Only count the asserted triples that do not define any RDFS or RDF terms, or specify that some subject is of type RDFS Class or Property.
            asserted_triples = [(s, p, o) for (s, p, o) in g.triples((None,None,None)) if s not in rdfs_nodes and o not in rdfs_nodes]

            # for (s,p,o) in asserted_triples:
            #     if type(o) == URIRef:
            #         print g.qname(s), g.qname(p), g.qname(o)
            #     else:
            #         print g.qname(s), g.qname(p), o

            asserted = len(asserted_triples)

            for constraint_file in glob('../constraints/*.rq'):
                with open(constraint_file) as cf:
                    query = cf.read()

                constraint = os.path.basename(constraint_file)
                result = g.query(query)

                count = 0
                for r in result:
                    count += 1

                line[constraint] = count
                # print "{}: {}".format(constraint, count)

            try:
                DeductiveClosure(RDFS_Semantics, rdfs_closure = True, axiomatic_triples = False, datatype_axioms = False).expand(g)
            except:
                traceback.print_exc()

            inferred = len([(s, p, o) for (s, p, o) in g.triples((None,None,None))])
            class_use = len(set([s for (s, p, o) in g.triples((None, RDF.type, None)) if o not in rdfs_nodes]))
            property_use = len(set([p for (s, p, o) in asserted_triples]))

            try:
                with codecs.open('{}/{}.rq'.format(path, basename), "r", encoding='utf-8-sig', errors='ignore') as qf:
                    query = qf.read()

                # Remove CR
                query = query.replace('\r\n', '\n')
                # try:
                #     query = query.decode("utf-8-sig").encode('utf-8')
                # except:
                #     print "..."

                # This kills the RDFLib parser
                query = query.replace('prefix', 'PREFIX')

                try:
                    # adding triples to database
                    all_triples = g.serialize(format='turtle')
                except:
                    # but if something's wrong with the original graph, just use an empty string
                    all_triples = ""

                update(all_triples)

                # running query
                qresults = sparql(query)


                # If stardog gives an error, we set the qcount to -1,
                # and make sure that if rdflib does better, we overwrite that,
                # otherwise, we fallback to the -1 qcount of stardog.

                if qresults != -1:
                    # counting results
                    stardog_qcount = 0
                    for r in qresults:
                        stardog_qcount += 1
                else:
                    stardog_qcount = -1

                # clearing database
                update(all_triples, action='clear')

                try:
                    qresults = g.query(query)
                    qcount = 0
                    for r in qresults:
                        qcount += 1

                    # Use whichever is higher
                    if stardog_qcount > qcount:
                        qcount = stardog_qcount
                except:
                    qcount = stardog_qcount

            except IOError:
                print "Could not find query"
                qcount = -2
            except:
                print "Query failed"
                try:
                    print query
                except:
                    "..."
                print(traceback.format_exc())
                qcount = -1

            line['query'] = qcount

            line['asserted'] = asserted # asserted triples that could not be inferred,
            line['inferred'] = inferred # total triples after inference,
            line['baseline'] = baseline # minimal expected number of new triples (baseline) for file without schema
            line['subjects_objects'] = subjects_objects
            line['predicates'] = predicates
            line['inferred through schema'] = inferred-baseline-asserted # triples inferred through the schema
            line['hash'] = hexdigest

            line = grade(line)

            writer.writerow(line)
            del(g)


def grade(line):
    grade = 0  # Max is 11, so 11 is a 5.5
    if line['syntax'] == 1:  # Parsed OK
        grade += 11
    else:
        grade += 8  # We also give points just for providing the file
        # TODO: Check with Stardog as well!!!
        # TODO: (some students were able to upload to stardog, even with syntactic errors)
        # print 'syntax', grade
    if line['inferred through schema'] > 0:
        grade += 1
        # print 'inferred', grade
    if line['count_classes.rq'] >= 4:
        grade += 1
        # print 'classes', grade
    if line['count_instances.rq'] >= 4:
        grade += 1
        # print 'instances', grade
    if line['count_properties.rq'] >= 4:
        grade += 1
        # print 'properties', grade
    if line['count_rdfslabel.rq'] >= 12:
        grade += 1
        # print 'labels', grade
    if line['count_rdfssubclassof.rq'] >= 1:
        grade += 1
        # print 'subClassOf', grade
    if line['count_rdfssubpropertyof.rq'] >= 1:
        grade += 1
        # print 'subPropertyOf', grade
    if line['count_rdftype.rq'] >= 1:
        grade += 1
        # print "type", grade
    if line['count_rdfsdomain.rq'] >= 1 and line['count_rdfsrange.rq'] >= 1:
        grade += 1
        # print "domain/range", grade

    line['Assignment 2b |846597'] = grade

    if line['query'] > 0:  # Have results
        line['Assignment 2c |846599'] = 10
    elif line['query'] == 0:  # Does not have results
        line['Assignment 2c |846599'] = 5
    elif line['query'] == -1:  # Cannot be parsed
        line['Assignment 2c |846599'] = 3
    else:  # Does not exist
        line['Assignment 2c |846599'] = 0

    return line


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Grade Assignment 2')
    parser.add_argument('path', type=str,
                    help='the file path to the submitted files')
    args = parser.parse_args()
    check(args.path)
