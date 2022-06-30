from django.db import IntegrityError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from .serializers import TagSerializer


class TagViewMixin(GenericViewSet):
    """Mixin for ModelViewSets that support tagging."""

    @action(detail=True, methods=["post"], name="Add a tag", serializer_class=TagSerializer)
    def tags(self, request, **kwargs):
        obj = self.get_object()
        serializer = TagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # We enforce uniqueness of tags on a single model instance on the database level,
        # so try and catch any constrain violations to return a meaningful message in the response.
        try:
            obj.tags.create(**serializer.validated_data)
        except IntegrityError as exc:
            exc_msg = exc.args[0]
            msg = "Tag already exists." if "unique constraint" in exc_msg else exc_msg
            return Response(data={"error": msg}, status=status.HTTP_400_BAD_REQUEST)
        return Response(data={"text": "Tag created."}, status=status.HTTP_201_CREATED)

    @tags.mapping.delete
    def delete_tag(self, request, **kwargs):
        """Delete a tag from a build."""
        obj = self.get_object()
        if not request.data:
            obj.tags.all().delete()
            return Response(data={"text": "All tags deleted."})

        serializer = TagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tag_to_delete = obj.tags.filter(**serializer.validated_data).first()
        if tag_to_delete:
            tag_to_delete.delete()
            return Response(data={"text": "Tag deleted."})
        else:
            return Response(data={"text": "Tag not found; nothing deleted."})
