__all__ = ['TNSRobot']

import sqlalchemy as sa
from sqlalchemy.orm import relationship
from sqlalchemy_utils.types.encrypted.encrypted_type import EncryptedType, AesEngine
from sqlalchemy_utils.types import JSONType
from sqlalchemy.dialects.postgresql import ARRAY

import json

from baselayer.app.env import load_env
from baselayer.app.models import Base
from .group import accessible_by_group_members


_, cfg = load_env()


class TNSRobot(Base):
    """A TNS robot entry."""

    create = read = update = delete = accessible_by_group_members

    group_id = sa.Column(
        sa.ForeignKey('groups.id', ondelete='CASCADE'),
        index=True,
        doc='The ID of the Group the TNS robot is associated with.',
        nullable=False,
    )
    group = relationship(
        'Group',
        back_populates='tnsrobots',
        doc='The Group the TNS robot is associated with.',
    )

    bot_name = sa.Column(sa.String, doc="Name of the TNS bot.", nullable=False)
    bot_id = sa.Column(sa.Integer, doc="ID of the TNS bot.", nullable=False)
    source_group_id = sa.Column(
        sa.Integer, doc="Source group ID of the TNS bot.", nullable=False
    )

    _altdata = sa.Column(
        EncryptedType(JSONType, cfg['app.secret_key'], AesEngine, 'pkcs5')
    )

    auto_report_group_ids = sa.Column(
        ARRAY(sa.Integer),
        comment='List of group IDs to report from',
        nullable=True,
    )

    auto_reporters = sa.Column(sa.String, doc="Auto report reporters.", nullable=True)

    @property
    def altdata(self):
        if self._altdata is None:
            return {}
        else:
            return json.loads(self._altdata)

    @altdata.setter
    def altdata(self, value):
        self._altdata = value
