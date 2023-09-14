from django.db import IntegrityError
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from .serializers import TagSerializer


class TagViewMixin(GenericViewSet):
    """Mixin for ModelViewSets that support tagging."""

    @action(
        authentication_classes=[TokenAuthentication],
        detail=True,
        methods=["post"],
        name="Add or delete a tag",
        permission_classes=[IsAuthenticated],
        serializer_class=TagSerializer,
    )
    def tags(self, request: Request, **kwargs: dict) -> Response:
        """Add a tag."""
        # TODO: self.get_object() doesn't work here??
        #  It tries to look up the last part of the URL as a UUID: "components/some_uuid/TAGS"
        #  instead of the middle part: "components/SOME_UUID/tags"
        # obj = self.get_object()
        obj = self.get_queryset().get()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # We enforce uniqueness of tags on a single model instance on the database level,
        # so try and catch any constraint violations to return a meaningful message in the response.
        try:
            tag = obj.tags.create(**serializer.validated_data)
        except IntegrityError as exc:
            exc_msg = exc.args[0]
            msg = "Tag already exists." if "unique constraint" in exc_msg else exc_msg
            status_code: int = status.HTTP_400_BAD_REQUEST
        else:
            msg = f"Created tag {tag.name}"
            # Append tag value to success message we return, if present
            if tag.value:
                msg = f"{msg}: {tag.value}"
            status_code = status.HTTP_201_CREATED

        return Response(data={"detail": msg}, status=status_code)

    @tags.mapping.delete
    def delete_tag(self, request: Request, **kwargs: dict) -> Response:
        """Delete a tag."""
        # obj = self.get_object()
        obj = self.get_queryset().get()
        if not request.data:
            tags_to_delete = obj.tags.get_queryset()
        else:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            tags_to_delete = obj.tags.filter(**serializer.validated_data)

        deleted_count, deleted_dict = tags_to_delete.delete()
        response = Response(
            data={"detail": f"{deleted_count} tag(s) and related models deleted: {deleted_dict}"}
        )
        if not deleted_count:
            # Message will say "0 tags deleted", so use a 404 code in this case
            response.status_code = status.HTTP_404_NOT_FOUND
        return response
