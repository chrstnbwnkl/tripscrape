import json
import re
from random import random
from time import sleep

import psycopg2 as db
from bs4 import BeautifulSoup as bs
from dotenv import dotenv_values
from requests import get

import selenium_utils
from tripscrape import Attraction, Review, Scraper, User


class ReviewScraper(Scraper):
    """
    A scraper of reviews (extends the Scraper base class)

    Attributes
    ----------

    db_conn : psycopg2.connection()
        a psycopg2 connection to PostgreSQL
    place_id : int
        a TripAdvisor place ID
    base_url : string
        The base url to be formatted
    search_type : str
        the search type of the scraper (defaults to "reviews")
    attr_types : tuple
        the types of attributes to be scraped (defaults to ("Sights & Landmarks))
    """

    def __init__(
        self,
        db_conn=None,
        db_iter_conn=None,
        place_id=186338,
        base_url="https://www.tripadvisor.com",
        search_type="reviews",
        attr_types=("Sights & Landmarks"),
    ):
        super().__init__(db_conn, place_id, base_url)
        self.search_type = search_type
        self._attr_types = attr_types
        self.db_iter_conn = db_iter_conn
        self.db_iter_cur = db_iter_conn.cursor()

    @property
    def attr_types(self):
        return self._attr_types

    @attr_types.setter
    def attr_types(self, value):
        self._attr_types = value

    def get_num_pages(self, soup):
        return super().get_num_pages(soup, search_type=self.search_type)

    def generate_page_links(self, url, amount):
        return super().generate_page_links(
            amount=amount, search_type=self.search_type, url=url
        )

    def read_attractions(self):
        """
        Read attractions from the attractions table in the database.
        """
        query_template = "SELECT id, url FROM attractions WHERE scraped = False"

        if self.attr_types == "all":
            return self.db_iter_cur.execute(query_template)
        elif len(self.attr_types) == 1:
            query_template += (
                ' WHERE attr_type IN (%s) AND scraped = False ORDER BY "id" DESC'
            )
            return self.db_iter_cur.execute(query_template, (self.attr_types,))
        elif len(self.attr_types) > 1:
            query_template += (
                ' WHERE attr_type IN %s AND scraped = False ORDER BY "id" DESC'
            )
            # query_template += " WHERE id = 187676"
            return self.db_iter_cur.execute(query_template, (self.attr_types,))

    def update_attraction(self, attr):
        """
        Update an attraction in the PostgreSQL database

        Parameters
        ----------
        attr: Attraction
            an Attraction instance
        """
        querystring = self.db_cur.mogrify(
            """UPDATE attractions SET geom = ST_SetSRID(ST_MakePoint(%s, %s),4326), num_reviews = %s WHERE id = %s;""",
            (attr.location[1], attr.location[0], json.dumps(attr.num_reviews), attr.ID),
        )

        return super().update_record(querystring)

    def update_review(self, review):
        """
        Update a review in the PostgreSQL database

        Parameters
        ----------
        review: Review
            a Review instance
        """
        querystring = self.db_cur.mogrify(
            """INSERT INTO reviews (ID, title, rating, date, "full", attr_ID, user_profile) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING;""",
            tuple(review.__dict__.values()),
        )
        return super().update_record(querystring)

    def update_user(self, user):
        """
        Update a user in the PostgreSQL database

        Parameters
        ----------
        user: User
            a User instance
        """
        print("Updating user {}".format(user.profile))
        if user.profile != None:
            querystring = self.db_cur.mogrify(
                """INSERT INTO users (profile, location, contributions, helpful_votes) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                tuple(user.__dict__.values()),
            )
            return super().update_record(querystring)
        else:
            print("Empty user information")
            return

    def get_attr_details(self, url):
        """
        Wrapper method for selenium_utils.get_attr_details()

        Parameters
        ----------
        url : str
            the attraction's url

        """
        return selenium_utils.get_attr_details(url)

    def print_missing_info(self, info_type, attr_ID, page_no, review_no, url):
        """
        Prints a message to stdout about missing information

        Parameters
        ----------
        info_type : str
            the type of information missing
        attr_ID : int
            the attraction id
        page_no : int
            the current page number
        review_no : int
            the current review number (ranges from 1-5)
        url : str
            the currently scraped attraction url
        """
        print(
            "No {} found at attraction {}, page {}, review {}.\nURL: {}".format(
                info_type, attr_ID, page_no + 1, review_no + 1, url
            )
        )

    def set_scraped(self, attr, boolean):
        """
        Sets the passed attraction's scraped column to the specified boolean value in the PostgreSQL data base.

        Parameters
        ----------
        attr : Attraction
            an Attraction instance
        boolean : bool
            a boolean value
        """
        query_template = "UPDATE attractions SET scraped = %s WHERE id = %s;"
        querystring = self.db_cur.mogrify(query_template, (boolean, attr.ID))
        print(f"{attr.ID} scraped set to {boolean}")
        return super().update_record(querystring)

    def traverse(self, val):
        """
        Traverses a nested dictionary and finds any review dictionaries

        Parameters
        ----------
        val : dict
            a dictionary from a TA static site

        """
        if isinstance(val, dict):
            for k, v in val.items():
                if k == "reviews":
                    yield v
                else:
                    yield from self.traverse(v)
        elif isinstance(val, list):
            for v in val:
                yield from self.traverse(v)

    def scrape_page(self, url, attr_ID, index):
        """
        Retrieves the web page from the passed url and scrapes it.

        url : str
            an attraction url
        attr_id : int
            a TripAdvisor attraction id
        index : int
            the current page of the attraction's reviews
        """
        print("Scraping page {}".format(url))

        while True:
            try:
                text = get(url).text
                data = re.search(r"window\.__WEB_CONTEXT__=(.*?});", text).group(1)
            except:
                print(f"Reloading {url}...")
                sleep(1)
                continue
            break

        data = data.replace("pageManifest", '"pageManifest"')
        data = json.loads(data)

        traverser = self.traverse(data)

        _exhausted = object()

        while next(traverser, _exhausted) == _exhausted:
            print(f"Weird stuff happening at {index}; retrying...")
            text = get(url).text
            data = re.search(r"window\.__WEB_CONTEXT__=(.*?});", text).group(1)
            data = data.replace("pageManifest", '"pageManifest"')
            data = json.loads(data)
            traverser = self.traverse(data)

        for reviews in self.traverse(data):
            if reviews:
                for ridx, r in enumerate(reviews):
                    review = Review()
                    user = User()

                    review.ID = r["id"]
                    review.title = r["title"]
                    review.rating = r["rating"]
                    review.full = r["text"]
                    review.attr_ID = attr_ID

                    review.date = r["publishedDate"]
                    try:
                        review.user_profile = r["userProfile"]["route"]["url"]
                        user.profile = review.user_profile
                    except:
                        self.print_missing_info(
                            "user profile", attr_ID, index, ridx, url
                        )

                    try:
                        user.location = json.dumps(r["userProfile"]["hometown"])
                    except:
                        self.print_missing_info(
                            "user location", attr_ID, index, ridx, url
                        )

                    try:
                        user.contributions = r["userProfile"]["contributionCounts"][
                            "sumAllUgc"
                        ]
                    except:
                        self.print_missing_info(
                            "user contributions", attr_ID, index, ridx, url
                        )

                    try:
                        user.helpful_votes = r["userProfile"]["contributionCounts"][
                            "helpfulVote"
                        ]
                    except:
                        self.print_missing_info("helpful", attr_ID, index, ridx, url)

                    self.update_user(user)
                    self.update_review(review)

            else:
                self.print_missing_info("reviews", attr_ID, index, -2, url)

    def do_scrape(self):
        """
        Retrieves the attractions to be scraped, scrapes its details, and then proceeds to scrape all of its reviews.
        """
        self.read_attractions()
        while True:
            row = self.db_iter_cur.fetchone()

            if row == None:
                break

            current_url = self.base_url + row[1]
            a = Attraction(row[0])
            a.location, a.num_reviews, number_of_pages = self.get_attr_details(
                current_url
            )
            print(a.location, a.num_reviews, number_of_pages)
            self.update_attraction(a)

            links = self.generate_page_links(current_url, number_of_pages)

            for index, link in enumerate(links):
                self.scrape_page(link, a.ID, index)
                sleep(1.3 + random())

            self.set_scraped(a, True)


def main():
    conn = db.connect(**dotenv_values())
    conn_iter = db.connect(**dotenv_values())
    r = ReviewScraper(db_conn=conn, db_iter_conn=conn_iter, attr_types="all")
    r.do_scrape()
    r.db_conn.close()
    r.db_iter_conn.close()


if __name__ == "__main__":
    main()
