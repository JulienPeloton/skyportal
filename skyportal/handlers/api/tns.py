import asyncio
import json
import tempfile
import time
import urllib

import arrow
import astropy.units as u
import requests
import sqlalchemy as sa
from astropy.time import Time, TimeDelta
from marshmallow.exceptions import ValidationError
from sqlalchemy.orm import scoped_session, sessionmaker
from tornado.ioloop import IOLoop

from baselayer.app.access import auth_or_token, permissions
from baselayer.app.env import load_env
from baselayer.app.flow import Flow
from baselayer.app.model_util import recursive_to_dict
from baselayer.log import make_log

from ...models import (
    DBSession,
    Group,
    Obj,
    Spectrum,
    SpectrumObserver,
    SpectrumReducer,
    TNSRobot,
    User,
)
from ...utils.tns import (
    get_IAUname,
    get_recent_TNS,
    post_tns,
    read_tns_photometry,
    read_tns_spectrum,
)
from ..base import BaseHandler
from .photometry import add_external_photometry
from .source import post_source
from .spectrum import post_spectrum

_, cfg = load_env()

Session = scoped_session(sessionmaker())

TNS_URL = cfg['app.tns.endpoint']
upload_url = urllib.parse.urljoin(TNS_URL, 'api/file-upload')
report_url = urllib.parse.urljoin(TNS_URL, 'api/bulk-report')
search_url = urllib.parse.urljoin(TNS_URL, 'api/get/search')
object_url = urllib.parse.urljoin(TNS_URL, 'api/get/object')

log = make_log('api/tns')


class TNSRobotHandler(BaseHandler):
    @auth_or_token
    def get(self, tnsrobot_id=None):
        """
        ---
        single:
          tags:
            - tnsrobots
          description: Retrieve a TNS robot
          parameters:
            - in: path
              name: tnsrobot_id
              required: true
              schema:
                type: integer
            - in: query
              name: groupID
              schema:
                type: integer
              description: |
                Filter by group ID
          responses:
            200:
               content:
                application/json:
                  schema: SingleTNSRobot
            400:
              content:
                application/json:
                  schema: Error
        multiple:
          tags:
            - tnsrobots
          description: Retrieve all TNS robots
          responses:
            200:
              content:
                application/json:
                  schema: ArrayOfTNSRobots
            400:
              content:
                application/json:
                  schema: Error
        """

        group_id = self.get_query_argument("groupID", None)

        with self.Session() as session:
            try:
                # get owned tnsrobots
                stmt = TNSRobot.select(session.user_or_token)

                if tnsrobot_id is not None:
                    try:
                        tnsrobot_id = int(tnsrobot_id)
                    except ValueError:
                        return self.error("TNSRobot ID must be an integer.")

                    stmt = stmt.where(TNSRobot.id == tnsrobot_id)
                    tnsrobot = session.scalars(stmt).first()
                    if tnsrobot is None:
                        return self.error(f'No TNS robot with ID {tnsrobot_id}')
                    return self.success(data=tnsrobot)

                elif group_id is not None:
                    try:
                        group_id = int(group_id)
                    except ValueError:
                        return self.error("Group ID must be an integer (if specified).")
                    stmt = stmt.where(TNSRobot.group_id == group_id)

                tns_robots = session.scalars(stmt).all()
                return self.success(data=tns_robots)
            except Exception as e:
                return self.error(f'Failed to retrieve TNS robots: {e}')

    @permissions(['Manage tnsrobots'])
    def post(self):
        """
        ---
        description: Post new TNS robot
        tags:
          - tnsrobots
        requestBody:
          content:
            application/json:
              schema: TNSRobotNoID
        responses:
          200:
            content:
              application/json:
                schema:
                  allOf:
                    - $ref: '#/components/schemas/Success'
                    - type: object
                      properties:
                        data:
                          type: object
                          properties:
                            id:
                              type: integer
                              description: New TNS robot ID
        """

        data = self.get_json()

        with self.Session() as session:

            try:
                tnsrobot = TNSRobot.__schema__().load(data=data)
            except ValidationError as e:
                return self.error(
                    f'Error parsing posted tnsrobot: "{e.normalized_messages()}"'
                )

            group = session.scalars(
                Group.select(session.user_or_token).where(Group.id == tnsrobot.group_id)
            ).first()
            if group is None:
                return self.error(f'No group with specified ID: {tnsrobot.group_id}')

            session.add(tnsrobot)
            session.commit()
            self.push(
                action='skyportal/REFRESH_TNSROBOTS',
                payload={"group_id": tnsrobot.group_id},
            )
            return self.success(data={"id": tnsrobot.id})

    @permissions(['Manage tnsrobots'])
    def put(self, tnsrobot_id):
        """
        ---
        description: Update TNS robot
        tags:
          - tnsrobots
        parameters:
          - in: path
            name: tnsrobot_id
            required: true
            schema:
              type: integer
        requestBody:
          content:
            application/json:
              schema: TNSRobot
        responses:
          200:
            content:
              application/json:
                schema: Success
        """

        data = self.get_json()

        # verify that the bot_id, bot_name, and source_group_id are not None and are integers (if specified)
        if 'bot_id' in data:
            try:
                data['bot_id'] = int(data['bot_id'])
            except ValueError:
                return self.error("TNS bot ID must be an integer (if specified).")
        if 'bot_name' in data:
            if (
                data['bot_name'] is None
                or data['bot_name'] == ''
                or not isinstance(data['bot_name'], str)
            ):
                return self.error(
                    "TNS bot name must be a non-empty string (if specified)."
                )
        if 'source_group_id' in data:
            try:
                data['source_group_id'] = int(data['source_group_id'])
            except ValueError:
                return self.error(
                    "TNS source group ID must be an integer (if specified)."
                )

        if 'auto_report_group_ids' in data:
            if isinstance(data['auto_report_group_ids'], str):
                try:
                    data['auto_report_group_ids'] = data['auto_report_group_ids'].split(
                        ','
                    )
                except Exception:
                    return self.error(
                        "TNS auto report group IDs must be a list (if specified)."
                    )
            if not isinstance(data['auto_report_group_ids'], list):
                return self.error(
                    "TNS auto report group IDs must be a list (if specified)."
                )
            for group_id in data['auto_report_group_ids']:
                try:
                    int(group_id)
                except ValueError:
                    return self.error(
                        "TNS auto report group IDs must be integers (if specified)."
                    )
            if len(data['auto_report_group_ids']) == 0:
                data['auto_reporters'] = ''

        with self.Session() as session:
            try:
                tnsrobot = session.scalars(
                    TNSRobot.select(session.user_or_token).where(
                        TNSRobot.id == tnsrobot_id
                    )
                ).first()
                if tnsrobot is None:
                    return self.error(f'No TNS robot with ID {tnsrobot_id}')

                if (
                    len(data.get('auto_report_group_ids', [])) > 0
                    and data.get('auto_reporters', '') in [None, '']
                    and tnsrobot.auto_reporters in [None, '']
                ):
                    return self.error(
                        "TNS auto reporters must be a non-empty string when auto report group IDs are specified."
                    )

                for key, val in data.items():
                    setattr(tnsrobot, key, val)
                session.commit()
                self.push(
                    action='skyportal/REFRESH_TNSROBOTS',
                    payload={"group_id": tnsrobot.group_id},
                )
                return self.success()
            except Exception as e:
                raise e
                return self.error(f'Failed to update TNS robot: {e}')

    @permissions(['Manage tnsrobots'])
    def delete(self, tnsrobot_id):
        """
        ---
        description: Delete TNS robot.
        tags:
          - tnsrobots
        parameters:
          - in: path
            name: tnsrobot_id
            required: true
            schema:
              type: string
        responses:
          200:
            content:
              application/json:
                schema: Success
        """
        try:
            tnsrobot_id = int(tnsrobot_id)
        except ValueError:
            return self.error("TNSRobot ID must be an integer.")

        with self.Session() as session:
            tnsrobot = session.scalars(
                TNSRobot.select(session.user_or_token, mode='delete').where(
                    TNSRobot.id == tnsrobot_id
                )
            ).first()
            if tnsrobot is None:
                return self.error(f'No TNS robot with ID {tnsrobot_id}')
            session.delete(tnsrobot)
            session.commit()
            self.push(
                action='skyportal/REFRESH_TNSROBOTS',
                payload={"group_id": tnsrobot.group_id},
            )
            return self.success()


def tns_bulk_retrieval(
    start_date,
    tnsrobot_id,
    user_id,
    group_ids=None,
    include_photometry=False,
    include_spectra=False,
):

    """Retrieve objects from TNS.
    start_date : str
        ISO-based start time
    tnsrobot_id : int
        TNSRobot ID
    user_id : int
        SkyPortal ID of User retrieving from TNS
    group_ids : List[int]
        List of groups to post TNS sources to
    include_photometry: boolean
        Include photometry available on TNS
    include_spectra : boolean
        Include spectra available on TNS
    """

    if Session.registry.has():
        session = Session()
    else:
        session = Session(bind=DBSession.session_factory.kw["bind"])

    user = session.scalar(sa.select(User).where(User.id == user_id))
    if group_ids is None:
        group_ids = [g.id for g in user.accessible_groups]

    try:
        tnsrobot = session.scalars(
            TNSRobot.select(user).where(TNSRobot.id == tnsrobot_id)
        ).first()
        if tnsrobot is None:
            raise ValueError(f'No TNSRobot available with ID {tnsrobot_id}')

        altdata = tnsrobot.altdata
        if not altdata:
            raise ValueError('Missing TNS information.')
        if 'api_key' not in altdata:
            raise ValueError('Missing TNS API key.')

        tns_headers = {
            'User-Agent': f'tns_marker{{"tns_id":{tnsrobot.bot_id},"type":"bot", "name":"{tnsrobot.bot_name}"}}'
        }

        tns_sources = get_recent_TNS(altdata['api_key'], tns_headers, start_date)
        if len(tns_sources) == 0:
            raise ValueError(f'No objects posted to TNS since {start_date}.')

        for source in tns_sources:
            s = session.scalars(Obj.select(user).where(Obj.id == source['id'])).first()
            if s is None:
                log(f"Posting {source['id']} as source")
                source['group_ids'] = group_ids
                post_source(source, user_id, session)

            tns_retrieval(
                source['id'],
                tnsrobot_id,
                user_id,
                include_photometry=include_photometry,
                include_spectra=include_spectra,
                parent_session=session,
            )
        session.commit()

    except Exception as e:
        log(f"Unable to retrieve TNS report for objects since {start_date}: {e}")
    finally:
        session.close()
        Session.remove()


def tns_retrieval(
    obj_id,
    tnsrobot_id,
    user_id,
    include_photometry=False,
    include_spectra=False,
    parent_session=None,
):
    """Retrieve object from TNS.
    obj_id : str
        Object ID
    tnsrobot_id : int
        TNSRobot ID
    user_id : int
        SkyPortal ID of User retrieving from TNS
    include_photometry: boolean
        Include photometry available on TNS
    include_spectra : boolean
        Include spectra available on TNS
    """

    if parent_session is None:
        if Session.registry.has():
            session = Session()
        else:
            session = Session(bind=DBSession.session_factory.kw["bind"])
    else:
        session = parent_session

    flow = Flow()

    user = session.scalar(sa.select(User).where(User.id == user_id))

    try:
        obj = session.scalars(Obj.select(user).where(Obj.id == obj_id)).first()
        if obj is None:
            raise ValueError(f'No object available with ID {obj_id}')

        tnsrobot = session.scalars(
            TNSRobot.select(user).where(TNSRobot.id == tnsrobot_id)
        ).first()
        if tnsrobot is None:
            raise ValueError(f'No TNSRobot available with ID {tnsrobot_id}')

        altdata = tnsrobot.altdata
        if not altdata:
            raise ValueError('Missing TNS information.')
        if 'api_key' not in altdata:
            raise ValueError('Missing TNS API key.')

        tns_headers = {
            'User-Agent': f'tns_marker{{"tns_id":{tnsrobot.bot_id},"type":"bot", "name":"{tnsrobot.bot_name}"}}'
        }

        _, tns_name = get_IAUname(
            altdata['api_key'], tns_headers, ra=obj.ra, dec=obj.dec
        )
        if tns_name is None:
            raise ValueError(f'{obj_id} not yet posted to TNS.')

        obj.tns_name = tns_name

        data = {
            'api_key': altdata['api_key'],
            'data': json.dumps(
                {
                    "objname": tns_name,
                    "photometry": "1" if include_photometry else "0",
                    "spectra": "1" if include_spectra else "0",
                }
            ),
        }

        r = requests.post(
            object_url,
            headers=tns_headers,
            data=data,
            allow_redirects=True,
            stream=True,
            timeout=10,
        )

        count = 0
        count_limit = 5
        while r.status_code == 429 and count < count_limit:
            log(
                f'TNS request rate limited: {str(r.json())}.  Waiting 30 seconds to try again.'
            )
            time.sleep(30)
            r = requests.post(
                object_url,
                headers=tns_headers,
                data=data,
                allow_redirects=True,
                stream=True,
                timeout=10,
            )
            count += 1

        if count == count_limit:
            raise ValueError('TNS request failed: request rate exceeded.')

        if r.status_code == 200:
            source_data = r.json().get("data", dict()).get("reply", dict())
            if source_data:
                obj.tns_info = source_data
                group_ids = [g.id for g in user.accessible_groups]

                if include_photometry and 'photometry' in source_data:
                    photometry = source_data['photometry']

                    failed_photometry = []
                    failed_photometry_errors = []

                    for phot in photometry:
                        try:
                            df, instrument_id = read_tns_photometry(phot, session)
                            data_out = {
                                'obj_id': obj_id,
                                'instrument_id': instrument_id,
                                'group_ids': group_ids,
                                **df.to_dict(orient='list'),
                            }
                            add_external_photometry(data_out, user)
                        except Exception as e:
                            failed_photometry.append(phot)
                            failed_photometry_errors.append(str(e))
                            log(f'Cannot read TNS photometry {str(phot)}: {str(e)}')
                            continue
                    if len(failed_photometry) > 0:
                        log(
                            f'Failed to retrieve {len(failed_photometry)}/{len(photometry)} TNS photometry for {obj_id} from TNS as {tns_name}: {str(list(set(failed_photometry_errors)))}'
                        )
                if include_spectra and 'spectra' in source_data:
                    group_ids = [g.id for g in user.accessible_groups]
                    spectra = source_data['spectra']

                    failed_spectra = []
                    failed_spectra_errors = []

                    for spectrum in spectra:
                        try:
                            data = read_tns_spectrum(spectrum, session)
                        except Exception as e:
                            log(f'Cannot read TNS spectrum {str(spectrum)}: {str(e)}')
                            continue
                        data["obj_id"] = obj_id
                        data["group_ids"] = group_ids
                        post_spectrum(data, user_id, session)

                    if len(failed_spectra) > 0:
                        log(
                            f'Failed to retrieve {len(failed_spectra)}/{len(spectra)} TNS spectra for {obj_id} from TNS as {tns_name}: {str(list(set(failed_spectra_errors)))}'
                        )

            log(f'Successfully retrieved {obj_id} from TNS as {tns_name}')
        else:
            log(f'Failed to retrieve {obj_id} from TNS: {r.content}')
        session.commit()

        flow.push(
            '*',
            'skyportal/REFRESH_SOURCE',
            payload={'obj_key': obj.internal_key},
        )

    except Exception as e:
        log(f"Unable to retrieve TNS report for {obj_id}: {e}")
    finally:
        if parent_session is not None:
            session.close()
            Session.remove()


class BulkTNSHandler(BaseHandler):
    @auth_or_token
    def post(self):
        """
        ---
        description: Retrieve objects from TNS
        tags:
          - objs
        requestBody:
          content:
            application/json:
              schema:
                type: object
                properties:
                  tnsrobotID:
                    type: int
                    description: |
                      TNS Robot ID.
                  startDate:
                    type: string
                    description: |
                      Arrow-parseable date string (e.g. 2020-01-01).
                      Filter by public_timestamp >= startDate.
                      Defaults to one day ago.
                  groupIds:
                    type: array
                    items:
                      type: integer
                    description: |
                      List of IDs of groups to indicate labelling for
                required:
                  - tnsrobotID
                  - groupIds
        responses:
          200:
            content:
              application/json:
                schema: Success
          400:
            content:
              application/json:
                schema: Error
        """

        data = self.get_json()
        group_ids = data.get("groupIds", None)
        if group_ids is None:
            return self.error('group_ids is required')
        elif type(group_ids) == str:
            group_ids = [int(x) for x in group_ids.split(",")]
        elif not type(group_ids) == list:
            return self.error('group_ids type not understood')

        start_date = data.get('startDate', None)
        if start_date is None:
            start_date = Time.now() - TimeDelta(1 * u.day)
        else:
            start_date = Time(arrow.get(start_date.strip()).datetime)

        tnsrobot_id = data.get("tnsrobotID", None)
        if tnsrobot_id is None:
            return self.error('tnsrobotID is required')

        include_photometry = data.get("includePhotometry", False)
        include_spectra = data.get("includeSpectra", False)

        with self.Session() as session:
            tnsrobot = session.scalars(
                TNSRobot.select(session.user_or_token).where(TNSRobot.id == tnsrobot_id)
            ).first()
            if tnsrobot is None:
                return self.error(f'No TNSRobot available with ID {tnsrobot_id}')

            altdata = tnsrobot.altdata
            if not altdata:
                return self.error('Missing TNS information.')
            if 'api_key' not in altdata:
                return self.error('Missing TNS API key.')

            try:
                loop = asyncio.get_event_loop()
            except Exception:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            IOLoop.current().run_in_executor(
                None,
                lambda: tns_bulk_retrieval(
                    start_date.isot,
                    tnsrobot.id,
                    self.associated_user_object.id,
                    group_ids=group_ids,
                    include_photometry=include_photometry,
                    include_spectra=include_spectra,
                ),
            )

            return self.success()


class ObjTNSHandler(BaseHandler):
    @auth_or_token
    def get(self, obj_id):
        """
        ---
        description: Retrieve an Obj from TNS
        tags:
          - objs
        parameters:
          - in: path
            name: obj_id
            required: true
            schema:
              type: string
        responses:
          200:
            content:
              application/json:
                schema: Success
          400:
            content:
              application/json:
                schema: Error
        """

        tnsrobot_id = self.get_query_argument("tnsrobotID", None)
        if tnsrobot_id is None:
            return self.error('tnsrobotID is required')

        include_photometry = self.get_query_argument("includePhotometry", False)
        include_spectra = self.get_query_argument("includeSpectra", False)

        with self.Session() as session:
            obj = session.scalars(
                Obj.select(session.user_or_token).where(Obj.id == obj_id)
            ).first()
            if obj is None:
                return self.error(f'No object available with ID {obj_id}')

            tnsrobot = session.scalars(
                TNSRobot.select(session.user_or_token).where(TNSRobot.id == tnsrobot_id)
            ).first()
            if tnsrobot is None:
                return self.error(f'No TNSRobot available with ID {tnsrobot_id}')

            altdata = tnsrobot.altdata
            if not altdata:
                return self.error('Missing TNS information.')
            if 'api_key' not in altdata:
                return self.error('Missing TNS API key.')

            try:
                loop = asyncio.get_event_loop()
            except Exception:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            IOLoop.current().run_in_executor(
                None,
                lambda: tns_retrieval(
                    obj.id,
                    tnsrobot.id,
                    self.associated_user_object.id,
                    include_photometry=include_photometry,
                    include_spectra=include_spectra,
                ),
            )

            return self.success()

    @auth_or_token
    def post(self, obj_id):
        """
        ---
        description: Post an Obj to TNS
        tags:
          - objs
        parameters:
          - in: path
            name: obj_id
            required: true
            schema:
              type: string
        responses:
          200:
            content:
              application/json:
                schema: Success
          400:
            content:
              application/json:
                schema: Error
        """

        with self.Session() as session:
            data = self.get_json()
            tnsrobotID = data.get('tnsrobotID')
            reporters = data.get('reporters', '')
            archival = data.get('archival', False)
            archival_comment = data.get('archivalComment', '')

            if tnsrobotID is None:
                return self.error('tnsrobotID is required')
            if reporters == '' or not isinstance(reporters, str):
                return self.error(
                    'reporters is required and must be a non-empty string'
                )

            obj = session.scalars(
                Obj.select(session.user_or_token).where(Obj.id == obj_id)
            ).first()
            if obj is None:
                return self.error(f'No object available with ID {obj_id}')

            tnsrobot = session.scalars(
                TNSRobot.select(session.user_or_token).where(TNSRobot.id == tnsrobotID)
            ).first()
            if tnsrobot is None:
                return self.error(f'No TNSRobot available with ID {tnsrobotID}')

            if archival is True:
                if len(archival_comment) == 0:
                    return self.error(
                        'If source flagged as archival, archival_comment is required'
                    )

            altdata = tnsrobot.altdata
            if not altdata:
                return self.error('Missing TNS information.')
            if 'api_key' not in altdata:
                return self.error('Missing TNS API key.')

            try:
                loop = asyncio.get_event_loop()
            except Exception:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            IOLoop.current().run_in_executor(
                None,
                lambda: post_tns(
                    obj_ids=[obj.id],
                    tnsrobot_id=tnsrobot.id,
                    user_id=self.associated_user_object.id,
                    reporters=reporters,
                    archival=archival,
                    archival_comment=archival_comment,
                    timeout=30,
                ),
            )

            return self.success()


class SpectrumTNSHandler(BaseHandler):
    @auth_or_token
    def post(self, spectrum_id):
        """
        ---
        description: Submit a (classification) spectrum to TNS
        tags:
          - spectra
        parameters:
          - in: path
            name: spectrum_id
            required: true
            schema:
              type: integer
          - in: query
            name: tnsrobotID
            schema:
              type: int
            required: true
            description: |
                SkyPortal TNS Robot ID
          - in: query
            name: classificationID
            schema:
              type: string
            description: |
                Classification ID (see TNS documentation at
                https://www.wis-tns.org/content/tns-getting-started
                for options)
          - in: query
            name: classifiers
            schema:
              type: string
            description: |
                List of those performing classification.
          - in: query
            name: spectrumType
            schema:
              type: string
            description: |
                Type of spectrum that this is. Valid options are:
                ['object', 'host', 'sky', 'arcs', 'synthetic']
          - in: query
            name: spectrumComment
            schema:
              type: string
            description: |
                Comment on the spectrum.
          - in: query
            name: classificationComment
            schema:
              type: string
            description: |
                Comment on the classification.
        responses:
          200:
            content:
              application/json:
                schema: SingleSpectrum
          400:
            content:
              application/json:
                schema: Error
        """

        data = self.get_json()
        tnsrobotID = data.get('tnsrobotID')
        classificationID = data.get('classificationID', None)
        classifiers = data.get('classifiers', '')
        spectrum_type = data.get('spectrumType', '')
        spectrum_comment = data.get('spectrumComment', '')
        classification_comment = data.get('classificationComment', '')

        if tnsrobotID is None:
            return self.error('tnsrobotID is required')

        with self.Session() as session:
            tnsrobot = session.scalars(
                TNSRobot.select(session.user_or_token).where(TNSRobot.id == tnsrobotID)
            ).first()
            if tnsrobot is None:
                return self.error(f'No TNSRobot available with ID {tnsrobotID}')

            altdata = tnsrobot.altdata
            if not altdata:
                return self.error('Missing TNS information.')

            spectrum = session.scalars(
                Spectrum.select(session.user_or_token).where(Spectrum.id == spectrum_id)
            ).first()
            if spectrum is None:
                return self.error(f'No spectrum with ID {spectrum_id}')

            spec_dict = recursive_to_dict(spectrum)
            spec_dict["instrument_name"] = spectrum.instrument.name
            spec_dict["groups"] = spectrum.groups
            spec_dict["reducers"] = spectrum.reducers
            spec_dict["observers"] = spectrum.observers
            spec_dict["owner"] = spectrum.owner

            external_reducer = session.scalars(
                SpectrumReducer.select(session.user_or_token).where(
                    SpectrumReducer.spectr_id == spectrum_id
                )
            ).first()
            if external_reducer is not None:
                spec_dict["external_reducer"] = external_reducer.external_reducer

            external_observer = session.scalars(
                SpectrumObserver.select(session.user_or_token).where(
                    SpectrumObserver.spectr_id == spectrum_id
                )
            ).first()
            if external_observer is not None:
                spec_dict["external_observer"] = external_observer.external_observer

            tns_headers = {
                'User-Agent': f'tns_marker{{"tns_id":{tnsrobot.bot_id},"type":"bot", "name":"{tnsrobot.bot_name}"}}'
            }

            tns_prefix, tns_name = get_IAUname(
                spectrum.obj.id, altdata['api_key'], tns_headers
            )
            if tns_name is None:
                return self.error('TNS name missing... please first post to TNS.')

            if spectrum.obj.redshift:
                redshift = spectrum.obj.redshift

            spectype_id = ['object', 'host', 'sky', 'arcs', 'synthetic'].index(
                spectrum_type
            ) + 1

            if spec_dict["altdata"] is not None:
                header = spec_dict["altdata"]
                exposure_time = header['EXPTIME']
            else:
                exposure_time = None

            wav = spec_dict['wavelengths']
            flux = spec_dict['fluxes']
            err = spec_dict['errors']

            filename = f'{spectrum.instrument.name}.{spectrum_id}'
            filetype = 'ascii'

            with tempfile.NamedTemporaryFile(
                prefix=filename,
                suffix=f'.{filetype}',
                mode='w',
            ) as f:
                if err is not None:
                    for i in range(len(wav)):
                        f.write(f'{wav[i]} \t {flux[i]} \t {err[i]} \n')
                else:
                    for i in range(len(wav)):
                        f.write(f'{wav[i]} \t {flux[i]}\n')
                f.flush()

                data = {'api_key': altdata['api_key']}

                if filetype == 'ascii':
                    files = [('files[]', (filename, open(f.name), 'text/plain'))]
                elif filetype == 'fits':
                    files = [
                        ('files[0]', (filename, open(f.name, 'rb'), 'application/fits'))
                    ]

                r = requests.post(
                    upload_url, headers=tns_headers, data=data, files=files
                )
                if r.status_code != 200:
                    return self.error(f'{r.content}')

                spectrumdict = {
                    'instrumentid': spectrum.instrument.tns_id,
                    'observer': spec_dict["observers"],
                    'reducer': spec_dict["reducers"],
                    'spectypeid': spectype_id,
                    'ascii_file': filename,
                    'fits_file': '',
                    'remarks': spectrum_comment,
                    'spec_proprietary_period': 0.0,
                    'obsdate': spec_dict['observed_at'],
                }
                if exposure_time is not None:
                    spectrumdict['exptime'] = exposure_time

                classification_report = {
                    'name': tns_name,
                    'classifier': classifiers,
                    'objtypeid': classificationID,
                    'groupid': tnsrobot.source_group_id,
                    'remarks': classification_comment,
                    'spectra': {'spectra-group': {'0': spectrumdict}},
                }
                if redshift is not None:
                    classification_report['redshift'] = redshift

                classificationdict = {
                    'classification_report': {'0': classification_report}
                }

                data = {
                    'api_key': altdata['api_key'],
                    'data': json.dumps(classificationdict),
                }

                r = requests.post(report_url, headers=tns_headers, data=data)
                if r.status_code == 200:
                    tns_id = r.json()['data']['report_id']
                    return self.success(data={'tns_id': tns_id})
                else:
                    return self.error(f'{r.content}')
