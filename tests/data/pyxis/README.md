# Pyxis test data

All changes in pyxis database are published to the messagebus by a sub-service called "snitch" ([datagrepper query](https://datagrepper.example.com/raw?category=snitch&delta=127800&rows_per_page=10&contains=manifest)).

* pyxis: stage
* containerImage ID: 64dccc5b6d82013739c4f7b8
* content manifest ID: 64dccc646d82013739c4f7e0
* based on image: quay.io/redhat-user-workloads/rhtap-build-tenant/build/image-controller@sha256:2fec403a6a1289567f19bffc8754793ea41724618a9c23979cf177cbcdb21c29

The manifest.json file is extracted from the pyxis graphql API ([prod](https://graphql.pyxis.example.com/graphql/), [stage](https://graphql.pyxis.stage.example.com/graphql/)) with the following query:

```graphql
{
  get_content_manifest(id: "64dccc646d82013739c4f7e0"){
    data {
      _id
      edges {
        components {
          data {
            name
            bom_ref
            supplier {
              name
              url
              contact {
                name
                email
              }
            }
            mime_type
            author
            publisher
            group
            version
            description
            scope
            hashes {
              alg
              content
            }
            licenses {
                license {
                    id
                }
            }
            copyright
            purl
            swid {
                tag_id
                name
            }
            external_references {
                url
                type
                comment
            }
            release_notes {
                type
                title
                description
            }
            build_dependency
            properties {
                name
                value
            }
            cpe
          }
        }
      }
    }
  }
}
```